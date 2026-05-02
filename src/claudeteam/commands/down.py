"""`claudeteam down` — opposite of `up`: stop daemons + tear down tmux.

Order matters: kill daemons first (so the watchdog doesn't respawn the
router we just killed), then kill tmux. Pid files get unlinked once the
process is confirmed dead.

Always best-effort — a missing pid file or already-dead process does
not raise. Returns 0 unless something we expected to be alive refused
to die.
"""
from __future__ import annotations

import os
import signal
import sys
import time

from claudeteam.runtime import config, paths, tmux
from claudeteam.util import error_exit, help_requested


def _kill_pid_file(name: str, pid_file) -> int:
    if not pid_file.exists():
        print(f"⏭  {name}: no pid file")
        return 0
    try:
        pid = int(pid_file.read_text(encoding="utf-8").strip())
    except (OSError, ValueError):
        print(f"⏭  {name}: corrupt pid file, removing")
        pid_file.unlink(missing_ok=True)
        return 0
    try:
        os.kill(pid, signal.SIGTERM)
    except ProcessLookupError:
        print(f"⏭  {name}: pid {pid} already dead")
        pid_file.unlink(missing_ok=True)
        return 0
    except PermissionError as e:
        return error_exit(f"❌ {name}: not allowed to kill pid {pid}: {e}")

    # Wait a few seconds for graceful shutdown
    for _ in range(30):
        try:
            os.kill(pid, 0)
        except ProcessLookupError:
            print(f"🛑 {name}: pid {pid} stopped")
            pid_file.unlink(missing_ok=True)
            return 0
        time.sleep(0.1)
    return error_exit(
        f"⚠️  {name}: pid {pid} still alive after 3s — manual SIGKILL may be needed")


def main(argv: list[str]) -> int:
    if help_requested(argv):
        print("usage: claudeteam down")
        return 0

    rc = 0
    # Kill watchdog FIRST so it doesn't respawn router behind our back
    rc |= _kill_pid_file("watchdog", paths.watchdog_pid_file())
    rc |= _kill_pid_file("router", paths.router_pid_file())

    session = config.session_name()
    if tmux.has_session(session):
        if tmux.kill_session(session):
            print(f"🛑 tmux session {session} killed")
        else:
            print(f"⚠️  failed to kill tmux session {session}", file=sys.stderr)
            rc |= 1
    else:
        print(f"⏭  tmux session {session} not running")

    print("✅ team down" if rc == 0 else "⚠️  team down with warnings")
    return 0 if rc == 0 else 1
