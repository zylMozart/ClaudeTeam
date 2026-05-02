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
        return True
    except (ProcessLookupError, PermissionError):
        return False
    except OSError:
        return False


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
    if pid is None:
        return False
    if not pid_alive(pid):
        return False
    cmdline = read_cmdline(pid)
    return spec.expected_cmdline in cmdline


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


def default_specs(*, project_root: Path | None = None) -> list[ProcessSpec]:
    """Built-in spec set: just the router for now.

    project_root defaults to cwd; pass an explicit one for tests / containers.
    """
    from claudeteam.runtime import paths
    root = project_root or Path.cwd()
    return [
        ProcessSpec(
            name="router",
            pid_file=paths.router_pid_file(),
            expected_cmdline="claudeteam",
            spawn_cmd=["claudeteam", "router"],
        ),
    ]
