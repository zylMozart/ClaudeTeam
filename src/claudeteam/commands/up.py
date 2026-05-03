"""`claudeteam up` — bring the whole team alive in one shot.

Composes existing primitives:
  1. `start` — tmux session + per-agent windows + CLI spawn (or lazy)
  2. `router` (detached) — long-running event subscriber
  3. `watchdog` (detached) — supervisor that re-spawns router if it dies

Skip steps where the resource is already alive (idempotent restart).
Returns 0 if everything ends up alive, 1 if any required step failed.
"""
from __future__ import annotations

import time

from claudeteam.commands import start as _start
from claudeteam.runtime import config, tmux, watchdog
from claudeteam.util import error_exit, help_requested


def _ensure_started() -> int:
    session = config.session_name()
    if tmux.has_session(session):
        print(f"⏭  tmux session {session} already running, skipping start")
        return 0
    return _start.main([])


def _ensure_daemon(spec: watchdog.ProcessSpec) -> int:
    if watchdog.is_alive(spec):
        print(f"⏭  {spec.name} already alive, skipping")
        return 0
    if not watchdog.respawn(spec):
        # respawn() already prints the OS error reason; up.py just sets rc=1.
        return error_exit(f"❌ failed to spawn {spec.name}")
    # Give the daemon a beat to write its pid file
    for _ in range(20):
        if spec.pid_file.exists():
            print(f"🚀 {spec.name} launched (pid {spec.pid_file.read_text().strip()})")
            return 0
        time.sleep(0.1)
    print(f"⚠️  {spec.name} launched but no pid file yet; check `claudeteam health`")
    return 0


def main(argv: list[str]) -> int:
    if help_requested(argv):
        print("usage: claudeteam up")
        return 0

    rc = _ensure_started()
    if rc != 0:
        return rc

    for spec in watchdog.all_known_specs():
        rc |= _ensure_daemon(spec)

    if rc == 0:
        print("✅ team up — run `claudeteam health` to verify")
    else:
        print("⚠️  team up with errors — see above; "
              "`claudeteam health` will list which daemons died")
    return rc
