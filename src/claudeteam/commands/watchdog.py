"""`claudeteam watchdog`

Long-running supervisor that keeps the router (and any future daemons)
alive.  Runs `runtime.watchdog.supervise` every CHECK_INTERVAL seconds
until SIGTERM / Ctrl-C.

Self-locks via state_dir/watchdog.pid so two watchdogs can't fight.
"""
from __future__ import annotations

import os
import signal
import sys
import time

from claudeteam.runtime import paths, watchdog


CHECK_INTERVAL_SECS = 30


def _self_pid_lock() -> bool:
    pf = paths.watchdog_pid_file()
    if pf.exists():
        try:
            old = int(pf.read_text(encoding="utf-8").strip())
            os.kill(old, 0)
            print(f"❌ another watchdog already running (pid {old})", file=sys.stderr)
            return False
        except (OSError, ValueError):
            pass  # stale; overwrite
    paths.ensure_state_dir()
    pf.write_text(str(os.getpid()), encoding="utf-8")
    return True


def _cleanup() -> None:
    try:
        pf = paths.watchdog_pid_file()
        if pf.exists() and pf.read_text(encoding="utf-8").strip() == str(os.getpid()):
            pf.unlink()
    except Exception:
        pass


def main(argv: list[str]) -> int:
    if argv and argv[0] in ("-h", "--help"):
        print("usage: claudeteam watchdog")
        return 0
    if not _self_pid_lock():
        return 1
    signal.signal(signal.SIGTERM, lambda *_: sys.exit(0))

    specs = watchdog.default_specs()
    states: dict = {}
    print(f"🐕 watchdog supervising {[s.name for s in specs]} every {CHECK_INTERVAL_SECS}s")

    try:
        while True:
            watchdog.supervise(specs, states)
            time.sleep(CHECK_INTERVAL_SECS)
    except KeyboardInterrupt:
        print("watchdog stopped")
        return 0
    finally:
        _cleanup()
