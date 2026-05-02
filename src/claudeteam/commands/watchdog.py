"""`claudeteam watchdog`

Long-running supervisor that keeps the router (and any future daemons)
alive.  Runs `runtime.watchdog.supervise` every CHECK_INTERVAL seconds
until SIGTERM / Ctrl-C.

Self-locks via state_dir/watchdog.pid so two watchdogs can't fight.
"""
from __future__ import annotations

import signal
import sys
import time

from claudeteam.runtime import paths, pidlock, watchdog


CHECK_INTERVAL_SECS = 30


def main(argv: list[str]) -> int:
    if argv and argv[0] in ("-h", "--help"):
        print("usage: claudeteam watchdog")
        return 0
    pid_file = paths.watchdog_pid_file()
    if not pidlock.acquire(pid_file, name="watchdog"):
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
        pidlock.release(pid_file)
