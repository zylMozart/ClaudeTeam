"""Unified alive / idle / ready detector for tmux-resident CLI agents.

Stage 2 of the messaging-pipeline plan
(``workspace/architect/messaging_pipeline_stage2_alive_idle_detector_2026-04-30.md``).
Replaces three scattered code paths with a single read-only probe object:

  • ``has_live_cli`` (router/wake) walking ``/proc`` for ``adapter.process_name()``
  • ``_lifecycle_pids_for_agent`` (agent_lifecycle.sh) doing the same
  • ``is_agent_idle`` / ``wait_cli_ui_ready`` (tmux_utils) consulting per-adapter
    ``busy_markers`` / ``ready_markers``

All three are now derived from tmux's own facts (``pane_current_command`` +
``pane_pid`` + ``capture-pane`` diff) — no ``/proc`` walks, no per-CLI marker
lists. Per-adapter ``process_names()`` is the one input the detector still
takes from CLI adapters, and it's a *set* of acceptable strings to tolerate
wrappers (``claude`` vs ``node``).

The detector is **read-only**: it never spawns / wakes / kills. State
transitions like ``DEAD → SPAWNING`` are still driven by
``agent_lifecycle.sh wake_agent``; the detector only reports what tmux sees.

Design ref: messaging_pipeline_stage2_alive_idle_detector_2026-04-30.md §2.
"""
from __future__ import annotations

import dataclasses
import enum
import hashlib
import os
import re
import subprocess
import time
from typing import Callable, Iterable, Optional

# Shared shell command names treated as "not yet a CLI" — pane front process
# is in shell mode, agent isn't running. Empty string covers tmux read-failure
# (display-message returns empty when the pane is between processes).
SHELL_NAMES = frozenset({"bash", "zsh", "sh", "dash", "fish", ""})

# CLI-agnostic ready hints that show up in nearly every TUI we run. Detector
# uses these instead of pulling per-adapter markers, so adapters can stay
# focused on spawn/resume metadata.
_READY_PLACEHOLDERS = (
    "tab to queue message",
    "? for shortcuts",
    "Send /help for help information",
    "Implement {feature}",
    "Summarize recent commits",
    "Find and fix a bug in @filename",
    "Use /skills to list available skills",
    "Explain this codebase",
)


class AgentLiveness(enum.Enum):
    UNKNOWN = "unknown"      # tmux can't see the window at all
    SHELL = "shell"          # pane front is bash/zsh/sh — CLI not running
    SPAWNING = "spawning"    # front cmd ≠ shell but also ≠ adapter.process_names
    LIVE = "live"            # pane_current_command ∈ adapter.process_names
    DEAD = "dead"            # window present but pane_pid resolves to nothing


@dataclasses.dataclass(frozen=True)
class LivenessProbe:
    liveness: AgentLiveness
    pane_current_command: str
    pane_pid: Optional[int]
    reason: str


@dataclasses.dataclass(frozen=True)
class IdleProbe:
    is_idle: bool
    sampled_frames: int
    fingerprint: str  # last frame's normalized hash, hex prefix
    reason: str


@dataclasses.dataclass(frozen=True)
class ReadyProbe:
    is_ready: bool
    waited_secs: float
    reason: str  # ok / timeout / shell / dead / unknown / window_missing


# ── pane normalization (single source of truth, inlined from tmux_utils) ─

_ANSI_RE = re.compile(r"\x1b\[[0-9;?]*[ -/]*[@-~]")
_DIGIT_RE = re.compile(r"\d+")


def normalize_pane(text: str) -> str:
    """Stabilize a ``capture-pane`` output for hash comparison.

    1. Strip ANSI control sequences.
    2. Drop ``\\r``.
    3. Right-strip each line (cursor wobble).
    4. Replace digit runs with empty string (timers / token counters /
       progress percentages — the dominant source of false busy verdicts).
    5. Drop the very last line (cursor / spinner usually parks there).

    Mirrors ``tmux_utils._normalize_pane`` so the legacy and detector paths
    produce identical hashes during the 3-day grayscale window.
    """
    s = _ANSI_RE.sub("", text or "").replace("\r", "")
    lines = [line.rstrip() for line in s.split("\n")]
    lines = [_DIGIT_RE.sub("", line) for line in lines]
    if len(lines) > 1:
        lines = lines[:-1]
    return "\n".join(lines)


# ── tmux call helpers (subprocess.run wrapped for testability) ───────────

# Default runner; tests inject their own.
def _default_runner(cmd):
    try:
        return subprocess.run(cmd, capture_output=True, text=True, timeout=5)
    except Exception:
        return None


class AgentDetector:
    """Read-only alive / idle / ready probe for one tmux pane.

    Args:
        session: tmux session name (e.g. ``"claudeteam"``).
        agent: tmux window name (= agent name).
        process_names: set of acceptable ``pane_current_command`` values
            indicating the CLI is running. Wrappers like ``node`` should be
            included alongside the CLI's own name. ``None`` = unknown CLI;
            liveness can still report ``SHELL`` / ``SPAWNING`` but never
            ``LIVE``.
        capture_lines: lines of pane scroll-back to capture for hashing /
            ready-detection. Larger window = more stable hash, but slower
            tmux call. 80 is the sweet spot from existing call sites.
        tmux_runner: callable taking a ``list[str]`` argv and returning an
            object with ``.returncode`` + ``.stdout`` (or ``None`` on
            error). Tests inject a stub; default runs ``subprocess.run``.
    """

    def __init__(
        self,
        session: str,
        agent: str,
        *,
        process_names: Optional[Iterable[str]] = None,
        capture_lines: int = 80,
        tmux_runner: Optional[Callable] = None,
    ):
        self.session = session
        self.agent = agent
        self.process_names = frozenset(process_names) if process_names else frozenset()
        self._capture_lines = capture_lines
        self._run = tmux_runner or _default_runner

    # ── tmux primitives ──────────────────────────────────────────────

    @property
    def target(self) -> str:
        return f"{self.session}:{self.agent}"

    def _has_session(self) -> bool:
        r = self._run(["tmux", "has-session", "-t", self.target])
        return bool(r and r.returncode == 0)

    def _display_message(self, fmt: str) -> str:
        r = self._run(["tmux", "display-message", "-t", self.target, "-p", fmt])
        if not r or r.returncode != 0:
            return ""
        return (r.stdout or "").strip()

    def _capture(self) -> str:
        r = self._run([
            "tmux", "capture-pane", "-t", self.target, "-p",
            "-S", f"-{self._capture_lines}",
        ])
        if not r or r.returncode != 0:
            return ""
        return r.stdout or ""

    # ── liveness ─────────────────────────────────────────────────────

    def liveness(self) -> LivenessProbe:
        """Classify the pane into one of five ``AgentLiveness`` states.

        Order of checks:
          1. ``tmux has-session`` → UNKNOWN if window is gone.
          2. ``pane_pid`` parse → DEAD if non-numeric / empty.
          3. ``pane_current_command`` ∈ shell names → SHELL.
          4. ``pane_current_command`` ∈ ``process_names`` → LIVE.
          5. otherwise → SPAWNING (intermediate process during npm / python
             import; resolves on the next probe).
        """
        if not self._has_session():
            return LivenessProbe(
                liveness=AgentLiveness.UNKNOWN,
                pane_current_command="",
                pane_pid=None,
                reason="tmux has-session failed",
            )
        pid_str = self._display_message("#{pane_pid}")
        try:
            pane_pid = int(pid_str) if pid_str else None
        except ValueError:
            pane_pid = None
        cmd = self._display_message("#{pane_current_command}")
        if pane_pid is None:
            return LivenessProbe(
                liveness=AgentLiveness.DEAD,
                pane_current_command=cmd,
                pane_pid=None,
                reason="pane_pid unreadable",
            )
        if cmd in SHELL_NAMES:
            return LivenessProbe(
                liveness=AgentLiveness.SHELL,
                pane_current_command=cmd,
                pane_pid=pane_pid,
                reason=f"front cmd '{cmd}' is shell",
            )
        if cmd in self.process_names:
            return LivenessProbe(
                liveness=AgentLiveness.LIVE,
                pane_current_command=cmd,
                pane_pid=pane_pid,
                reason=f"front cmd '{cmd}' matches adapter process_names",
            )
        return LivenessProbe(
            liveness=AgentLiveness.SPAWNING,
            pane_current_command=cmd,
            pane_pid=pane_pid,
            reason=f"front cmd '{cmd}' not yet in process_names",
        )

    def is_alive(self) -> bool:
        """Convenience: True iff liveness == LIVE."""
        return self.liveness().liveness == AgentLiveness.LIVE

    # ── idle ─────────────────────────────────────────────────────────

    def is_idle(
        self,
        *,
        samples: int = 10,
        interval_ms: int = 300,
    ) -> IdleProbe:
        """Sample the pane ``samples`` times at ``interval_ms`` apart.

        Each frame is normalized then hashed; consecutive identical hashes
        across the full sample run = idle. First diff aborts the loop and
        returns busy. Total wall time ≈ ``(samples - 1) * interval_ms``;
        the defaults (10 × 300 ms ≈ 2.7 s) match existing
        ``inject_when_idle`` budgets and are tuned in §2.4 of the design.
        """
        samples = max(1, samples)
        interval = max(0, interval_ms) / 1000.0
        last_hash = ""
        for i in range(samples):
            raw = self._capture()
            if not raw:
                # Capture failure is fail-safe: treat as busy so we don't
                # inject into a broken pane.
                return IdleProbe(
                    is_idle=False,
                    sampled_frames=i + 1,
                    fingerprint=last_hash[:8],
                    reason="capture_pane failed",
                )
            h = hashlib.sha1(normalize_pane(raw).encode("utf-8")).hexdigest()
            if i > 0 and h != last_hash:
                return IdleProbe(
                    is_idle=False,
                    sampled_frames=i + 1,
                    fingerprint=h[:8],
                    reason="pane changed between frames",
                )
            last_hash = h
            if i < samples - 1 and interval > 0:
                time.sleep(interval)
        return IdleProbe(
            is_idle=True,
            sampled_frames=samples,
            fingerprint=last_hash[:8],
            reason=f"stable {samples} frames",
        )

    # ── ready ────────────────────────────────────────────────────────

    def wait_until_ready(
        self,
        *,
        timeout_s: float = 30.0,
        poll_interval_s: float = 0.5,
        ready_placeholders: Iterable[str] = _READY_PLACEHOLDERS,
    ) -> ReadyProbe:
        """Block up to ``timeout_s`` until the pane shows a CLI ready signal.

        Ready signal = liveness == LIVE *and* the pane tail contains any of
        ``ready_placeholders``. This is the post-spawn handshake check; it
        replaces ``tmux_utils.wait_cli_ui_ready`` for new call sites.
        Per-adapter marker lists are not consulted — the placeholders are
        CLI-agnostic and have proven sufficient across claude / codex /
        gemini / qwen / kimi.

        ``ready_placeholders`` is overridable for tests but production
        callers should use the module default to keep behavior consistent
        across CLIs.
        """
        deadline = time.time() + timeout_s
        start = time.time()
        last_reason = "timeout"
        while time.time() < deadline:
            probe = self.liveness()
            if probe.liveness == AgentLiveness.UNKNOWN:
                last_reason = "window_missing"
            elif probe.liveness == AgentLiveness.DEAD:
                return ReadyProbe(
                    is_ready=False,
                    waited_secs=time.time() - start,
                    reason="dead",
                )
            elif probe.liveness == AgentLiveness.SHELL:
                last_reason = "shell"
            elif probe.liveness == AgentLiveness.LIVE:
                pane = self._capture()
                if any(ph in pane for ph in ready_placeholders):
                    return ReadyProbe(
                        is_ready=True,
                        waited_secs=time.time() - start,
                        reason="ok",
                    )
                last_reason = "live_no_placeholder"
            else:  # SPAWNING
                last_reason = "spawning"
            time.sleep(poll_interval_s)
        return ReadyProbe(
            is_ready=False,
            waited_secs=time.time() - start,
            reason=last_reason,
        )


# ── env-var resolved defaults (§2.4 of the design) ───────────────────

def default_samples() -> int:
    """``CLAUDETEAM_IDLE_SAMPLE_COUNT`` env override; defaults to 10."""
    try:
        return int(os.environ.get("CLAUDETEAM_IDLE_SAMPLE_COUNT", "10"))
    except ValueError:
        return 10


def default_interval_ms() -> int:
    """``CLAUDETEAM_IDLE_SAMPLE_INTERVAL_MS`` env override; defaults to 300."""
    try:
        return int(os.environ.get("CLAUDETEAM_IDLE_SAMPLE_INTERVAL_MS", "300"))
    except ValueError:
        return 300


def legacy_mode_enabled() -> bool:
    """``CLAUDETEAM_DETECTOR_LEGACY=1`` → integration shims should use the
    pre-stage-2 paths instead of the detector. 3-day grayscale switch."""
    return os.environ.get("CLAUDETEAM_DETECTOR_LEGACY", "").strip() in (
        "1", "true", "yes", "on", "TRUE", "True",
    )
