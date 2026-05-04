"""`claudeteam up` — bring the whole team alive in one shot.

Composes existing primitives:
  1. `start` — tmux session + per-agent windows + CLI spawn (or lazy)
  2. `router` (detached) — long-running event subscriber
  3. `watchdog` (detached) — supervisor that re-spawns router if it dies

Skip steps where the resource is already alive (idempotent restart).
Returns 0 if everything ends up alive, 1 if any required step failed.

Round-62 fast-fail guard: each daemon spawn waits up to 3s for its
pid file to appear under `state_dir/`. If no pid file shows up
(daemon `error_exit`'d before pidlock — usually missing chat_id /
no team agents / port collision), `up` reports the boot failure and
returns rc=1 instead of silently saying `✅ team up`. Operator runs
`claudeteam <name>` directly to see the actual error message.
"""
from __future__ import annotations

import time

from claudeteam.commands import start as _start
from claudeteam.runtime import config, tmux, watchdog
from claudeteam.util import error_exit, maybe_print_help


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
    # Wait up to 3s for the daemon to write its pid file. The pidlock
    # acquire happens immediately after early config validation in the
    # daemon's main(), so 3s is generous for a healthy spawn. If the
    # daemon fast-failed (e.g. missing chat_id, no team agents, port
    # collision), no pid file appears — treat that as failure rather
    # than silently saying "team up". Round-62 caught this: a missing
    # chat_id used to give "⚠️ launched but no pid file yet" + rc=0,
    # masking the boot failure from `claudeteam up`'s exit code.
    for _ in range(30):
        if spec.pid_file.exists():
            print(f"🚀 {spec.name} launched (pid {spec.pid_file.read_text().strip()})")
            return 0
        time.sleep(0.1)
    return error_exit(
        f"❌ {spec.name} launched but didn't write a pid file in 3s — "
        f"likely fast-failed at startup; check `claudeteam {spec.name}` "
        f"directly to see the error")


def main(argv: list[str]) -> int:
    if maybe_print_help(argv, "usage: claudeteam up"):
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
