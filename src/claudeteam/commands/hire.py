"""`claudeteam hire <agent>`

Add a single agent to a running team: create the tmux window, spawn its
CLI, mark status.  Errors out if the team isn't running yet (use
`claudeteam start` first).
"""
from __future__ import annotations

from claudeteam.runtime import config, lifecycle, tmux
from claudeteam.util import error_exit, usage_error, warn


USAGE = "usage: claudeteam hire <agent>"


def main(argv: list[str]) -> int:
    if len(argv) < 1:
        return usage_error(USAGE)
    agent = argv[0]

    try:
        cfg = config.agent_config(agent)
    except KeyError:
        return error_exit(f"❌ unknown agent: {agent} (not in team.json)")
    cli = cfg.get("cli", "claude-code")

    session = config.session_name()
    if not tmux.has_session(session):
        return error_exit(
            f"❌ tmux session {session} not running; run `claudeteam start` first")

    target = tmux.Target(session, agent)
    if tmux.has_window(target):
        print(f"⚠️  {agent} already has a pane")
        return 0
    if not tmux.new_window(target):
        return error_exit(f"❌ failed to create window for {agent}")

    outcome = lifecycle.provision_pane(agent, target)
    if outcome == lifecycle.LAZY:
        print(f"✅ hired (lazy): {agent} ({cli}) → {target}")
        return 0
    if outcome == lifecycle.CONFIG_ERROR:
        return error_exit(
            f"❌ {agent}: bad cli config in team.json (see warning above); "
            f"hire aborted, fix team.json and retry")
    if outcome == lifecycle.SPAWN_FAILED:
        return error_exit(f"❌ failed to spawn CLI in {agent} pane")
    if outcome == lifecycle.READY_NO_INIT:
        warn(f"⚠️  {agent} CLI didn't show ready marker in 20s; "
             f"identity init prompt skipped")
    print(f"✅ hired: {agent} ({cli}) → {target}")
    return 0
