"""Single-instance pid file lock for daemon commands.

Both `claudeteam router` and `claudeteam watchdog` need to:
  - claim a per-daemon pid file under $CLAUDETEAM_STATE_DIR
  - refuse to start if another live process already holds it
  - silently overwrite a stale lock left by a crashed previous run
  - clean up the lock on graceful exit (only if we still own it)

Watchdog already had this; router was just unconditionally overwriting,
which let two routers race their inserts into Feishu's event stream.
Hoisting unifies behavior and removes ~30 lines of dup.
"""
from __future__ import annotations

import os
import sys
from pathlib import Path

from claudeteam.runtime import paths


def acquire(pid_file: Path, *, name: str = "") -> bool:
    """Claim `pid_file` for the current process.

    Returns True on success. Returns False if another **live** process
    already owns the file — prints to stderr in that case. Stale locks
    (pid file present but the recorded pid is dead) are quietly
    overwritten on the assumption a previous run crashed.
    """
    if pid_file.exists():
        try:
            old = int(pid_file.read_text(encoding="utf-8").strip())
            os.kill(old, 0)
            print(f"❌ another {name or 'instance'} already running (pid {old})",
                  file=sys.stderr)
            return False
        except (OSError, ValueError):
            pass  # stale: overwrite
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
