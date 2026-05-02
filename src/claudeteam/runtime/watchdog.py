"""Single-file process supervisor.

Replaces the old `supervision/` 11-file decomposition (~580 LOC) with one
~200 LOC module.  Each ProcessSpec describes a daemon to keep alive; a
single `supervise()` sweep checks every spec, spawns or backs off as
appropriate, and writes one heartbeat line.

State machine per spec:
    alive            → noop
    dead, ok-to-spawn → respawn
    dead, in cooldown → noop, count down
    respawn fails N times → enter cooldown for cooldown_secs

Liveness is `kill(pid, 0) AND cmdline contains expected_cmdline`.  The
cmdline check defends against PID reuse (memory: ClaudeTeam Bug 14).
"""
from __future__ import annotations

import os
import subprocess
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Callable

from claudeteam.runtime import paths


# ── per-process spec & state ──────────────────────────────────────


@dataclass(frozen=True)
class ProcessSpec:
    name: str                 # human-readable label, e.g. "router"
    pid_file: Path            # where the daemon writes its pid
    expected_cmdline: str     # substring that must appear in /proc/<pid>/cmdline
    spawn_cmd: list[str]      # subprocess.Popen argv to (re)start the daemon
    max_retries: int = 3
    cooldown_secs: int = 600


@dataclass
class ProcessState:
    name: str
    fail_count: int = 0
    cooldown_until: float = 0.0
    last_action: str = ""     # "alive" / "respawned" / "cooldown" / "fail"


# ── liveness check ────────────────────────────────────────────────


def _read_pid(pid_file: Path) -> int | None:
    try:
        return int(pid_file.read_text(encoding="utf-8").strip())
    except (OSError, ValueError):
        return None


def _pid_alive(pid: int) -> bool:
    try:
        os.kill(pid, 0)
    except OSError:
        # Covers ProcessLookupError (no such pid), PermissionError (not ours),
        # and other OSError variants. Either way the process isn't usable.
        return False
    return True


def _read_cmdline(pid: int) -> str:
    try:
        # Linux /proc; on macOS /proc doesn't exist so we fall back to ps
        path = f"/proc/{pid}/cmdline"
        if os.path.exists(path):
            with open(path, "rb") as fh:
                return fh.read().decode("utf-8", errors="ignore").replace("\0", " ")
        # macOS fallback
        r = subprocess.run(["ps", "-p", str(pid), "-o", "command="],
                           capture_output=True, text=True, timeout=3)
        return r.stdout.strip() if r.returncode == 0 else ""
    except (OSError, subprocess.TimeoutExpired):
        return ""


def is_alive(spec: ProcessSpec, *,
             read_pid: Callable = _read_pid,
             pid_alive: Callable = _pid_alive,
             read_cmdline: Callable = _read_cmdline) -> bool:
    pid = read_pid(spec.pid_file)
    if pid is None or not pid_alive(pid):
        return False
    return spec.expected_cmdline in read_cmdline(pid)


# ── respawn ────────────────────────────────────────────────────────


def respawn(spec: ProcessSpec, *,
            popen: Callable = subprocess.Popen) -> bool:
    try:
        popen(spec.spawn_cmd, start_new_session=True,
              stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        return True
    except (OSError, ValueError) as e:
        print(f"  ⚠️ {spec.name} respawn failed: {e}")
        return False


# ── one sweep ──────────────────────────────────────────────────────


def supervise(specs: list[ProcessSpec],
              states: dict[str, ProcessState], *,
              now: Callable = time.time,
              alive_check: Callable = is_alive,
              respawn_fn: Callable = respawn,
              log: Callable = print) -> None:
    """Walk every spec once, decide alive / respawn / cooldown.

    Mutates `states` in place.  Caller wraps this in their own loop.
    """
    t = now()
    for spec in specs:
        st = states.setdefault(spec.name, ProcessState(spec.name))

        # cooldown: skip until expiry
        if t < st.cooldown_until:
            st.last_action = "cooldown"
            continue

        if alive_check(spec):
            if st.last_action != "alive":
                log(f"✅ {spec.name} alive")
            st.fail_count = 0
            st.last_action = "alive"
            continue

        # dead: respawn
        if respawn_fn(spec):
            log(f"🔁 {spec.name} respawned (fail_count was {st.fail_count})")
            st.last_action = "respawned"
            # don't reset fail_count yet — wait until next sweep proves it stuck
            continue

        st.fail_count += 1
        if st.fail_count >= spec.max_retries:
            st.cooldown_until = t + spec.cooldown_secs
            st.fail_count = 0  # reset for after cooldown
            st.last_action = "cooldown"
            log(f"⛔ {spec.name} entering {spec.cooldown_secs}s cooldown after {spec.max_retries} fails")
        else:
            st.last_action = "fail"
            log(f"❌ {spec.name} respawn failed ({st.fail_count}/{spec.max_retries})")


# ── default specs for ClaudeTeam ──────────────────────────────────


def _claudeteam_spec(name: str, pid_file: Path) -> ProcessSpec:
    """Build a ProcessSpec for a `claudeteam <name>` daemon. The cmdline-match
    string is just `\"claudeteam\"` so any process whose argv contains the
    word counts — defends against PID reuse, doesn't lock to argv shape."""
    return ProcessSpec(
        name=name,
        pid_file=pid_file,
        expected_cmdline="claudeteam",
        spawn_cmd=["claudeteam", name],
    )


def default_specs() -> list[ProcessSpec]:
    """Specs the watchdog supervises. Just the router — the watchdog
    doesn't supervise itself."""
    return [_claudeteam_spec("router", paths.router_pid_file())]


def all_known_specs() -> list[ProcessSpec]:
    """Every daemon ClaudeTeam ships, for `health` and similar audits.
    Includes the watchdog itself so health can verify its lock file
    matches a live process."""
    return [
        _claudeteam_spec("router", paths.router_pid_file()),
        _claudeteam_spec("watchdog", paths.watchdog_pid_file()),
    ]
