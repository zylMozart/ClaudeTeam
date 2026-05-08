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
import time

from claudeteam.runtime import config, pidlock, tmux, watchdog
from claudeteam.util import error_exit, maybe_print_help, warn


def _kill_pid_file(name: str, pid_file) -> int:
    if not pid_file.exists():
        print(f"⏭  {name}: no pid file")
        return 0
    pid = pidlock.read_pid(pid_file)
    if pid is None:
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

    # SIGTERM grace, then escalate to SIGKILL. Smoke v3: 3s wasn't enough
    # for router/watchdog mid-lark-cli to flush — 10s catches the slow
    # path; SIGKILL fallback guarantees `compose down` doesn't punt to
    # the operator.
    for _ in range(100):
        try:
            os.kill(pid, 0)
        except ProcessLookupError:
            print(f"🛑 {name}: pid {pid} stopped")
            pid_file.unlink(missing_ok=True)
            return 0
        time.sleep(0.1)
    try:
        os.kill(pid, signal.SIGKILL)
    except ProcessLookupError:
        print(f"🛑 {name}: pid {pid} stopped")
        pid_file.unlink(missing_ok=True)
        return 0
    for _ in range(20):
        try:
            os.kill(pid, 0)
        except ProcessLookupError:
            print(f"🛑 {name}: pid {pid} stopped (after SIGKILL)")
            pid_file.unlink(missing_ok=True)
            return 0
        time.sleep(0.1)
    return error_exit(
        f"⚠️  {name}: pid {pid} still alive after 12s SIGTERM+SIGKILL — investigate manually")


def main(argv: list[str]) -> int:
    if maybe_print_help(argv, "usage: claudeteam down"):
        return 0

    rc = 0
    # Kill in reverse-of-startup order so the watchdog can't respawn
    # the router we just killed. all_known_specs is router-then-watchdog;
    # reversed → watchdog first.
    for spec in reversed(watchdog.all_known_specs()):
        rc |= _kill_pid_file(spec.name, spec.pid_file)

    session = config.session_name()
    if tmux.has_session(session):
        if tmux.kill_session(session):
            print(f"🛑 tmux session {session} killed")
        else:
            warn(f"⚠️  failed to kill tmux session {session}")
            rc |= 1
    else:
        print(f"⏭  tmux session {session} not running")

    print("✅ team down" if rc == 0 else "⚠️  team down with warnings")
    return rc
