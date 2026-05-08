"""Pid file primitives + single-instance daemon lock.

Public surface:
  - `read_pid(pid_file)` → int | None — parse a pid file safely
  - `pid_alive(pid)`     → bool       — kill -0 wrapper, OSError = False
  - `acquire(pid_file)`  → bool       — claim or reject based on liveness
  - `release(pid_file)`  → None       — drop the lock on graceful exit

`acquire` / `release` are the daemon lifecycle pair (`claudeteam router`
and `claudeteam watchdog` use them). `read_pid` / `pid_alive` are the
primitives that grew out — `commands/down._kill_pid_file`,
`watchdog.is_alive`, `commands/health._check_daemon` all need to
inspect "the pid that owns this file, if any" without claiming the
lock, and they used to each reimplement the int-parse + os.kill(0)
fences.

Stale locks (pid file present but the recorded pid is dead) are
quietly overwritten by `acquire` on the assumption a previous run
crashed.
"""
from __future__ import annotations

import os
from pathlib import Path

from claudeteam.runtime import paths
from claudeteam.util import warn


def read_pid(pid_file: Path) -> int | None:
    """Parse `pid_file` as an integer. Returns None when the file is
    missing, unreadable, or contains non-int content.

    Used wherever code needs "the pid that owns this file, if any" —
    `acquire` here, `watchdog.is_alive`, `commands/down._kill_pid_file`,
    `commands/health._check_daemon`. Centralised so any future tweak
    (e.g. trimming a pid+timestamp format) lands in one place.
    """
    try:
        return int(pid_file.read_text(encoding="utf-8").strip())
    except (OSError, ValueError):
        return None


def pid_alive(pid: int) -> bool:
    """True if `pid` exists and we can signal it (kill 0).

    OSError covers ProcessLookupError (no such pid), PermissionError
    (not ours — but daemons here are always owned by the same user
    so this rarely fires), and other variants. Either way: not usable.
    """
    try:
        os.kill(pid, 0)
    except OSError:
        return False
    return True


def acquire(pid_file: Path, *, name: str = "",
            wait_for_release_s: float = 3.0) -> bool:
    """Claim `pid_file` for the current process.

    Returns True on success. Returns False if another **live** process
    already owns the file — prints to stderr in that case. Stale locks
    (pid file present but the recorded pid is dead) are quietly
    overwritten on the assumption a previous run crashed.

    `wait_for_release_s` short-circuits the SIGTERM-in-progress race:
    when an operator does `claudeteam down` immediately followed by
    `claudeteam up`, the previous router is mid-shutdown (signal
    handler running, pidlock not yet released) when the new router
    runs `acquire`. Without a wait, the new router sees the still-live
    old pid and refuses with "another instance already running".
    Spin-poll for up to a few seconds — long enough to ride out the
    typical sigterm cleanup, short enough that a genuinely-stuck
    other-instance still surfaces an error promptly. 2026-05-08
    fresh-host smoke caught this when an agent rapid-cycled the
    deploy for §6/§7/§9 tests.
    """
    if pid_file.exists():
        old = read_pid(pid_file)
        if old is not None and pid_alive(old):
            if wait_for_release_s > 0:
                import time
                deadline = time.monotonic() + wait_for_release_s
                while time.monotonic() < deadline:
                    if not pid_alive(old):
                        break
                    time.sleep(0.1)
            if pid_alive(old):
                warn(f"❌ another {name or 'instance'} already running (pid {old})")
                return False
        # else: missing-or-corrupt pid file, or stale lock from a dead
        # previous run — quietly overwrite below.
    paths.ensure_state_dir()
    pid_file.write_text(str(os.getpid()), encoding="utf-8")
    return True


def release(pid_file: Path) -> None:
    """Remove `pid_file` if it currently records our pid. Best-effort —
    swallows any I/O exception since this runs in a `finally` clause."""
    try:
        if (pid_file.exists()
                and pid_file.read_text(encoding="utf-8").strip() == str(os.getpid())):
            pid_file.unlink()
    except Exception:
        pass
