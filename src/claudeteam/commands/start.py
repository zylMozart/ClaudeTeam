"""`claudeteam start`

Bring up the whole team described in team.json: one tmux session, one
window per agent, each running its configured CLI.
"""
from __future__ import annotations

from claudeteam.runtime import config, lifecycle, tmux
from claudeteam.util import error_exit, maybe_print_help, warn


def main(argv: list[str]) -> int:
    if maybe_print_help(argv, "usage: claudeteam start"):
        return 0

    team = config.load_team()
    agents = team.get("agents", {})
    if not agents:
        return error_exit("❌ team.json has no agents")

    session = team.get("session", "ClaudeTeam")
    agent_list = sorted(agents)
    first = agent_list[0]

    if tmux.has_session(session):
        print(f"⚠️  session {session} already running; refusing to start over")
        return 1

    if not tmux.new_session(session, window=first):
        return error_exit(f"❌ failed to create tmux session {session}")
    print(f"🚀 created tmux session {session} (initial window: {first})")

    for agent in agent_list:
        target = tmux.Target(session, agent)
        if agent != first and not tmux.new_window(target):
            warn(f"⚠️  failed to create window for {agent}, skipping")
            continue
        # Re-use the team dict loaded above; `config.agent_config`
        # would re-read team config from disk per agent (no in-process
        # cache, by design). lifecycle.provision_pane has its own
        # internal hoist for the same reason.
        cli = agents.get(agent, {}).get("cli", "claude-code")
        outcome = lifecycle.provision_pane(agent, target)
        if outcome == lifecycle.LAZY:
            print(f"  → {agent} ({cli}) lazy-pane ready")
        elif outcome == lifecycle.SPAWN_FAILED:
            warn(f"⚠️  failed to spawn CLI in {agent} pane")
        elif outcome == lifecycle.CONFIG_ERROR:
            warn(f"⚠️  {agent} skipped: bad cli config in team.json")
        elif outcome == lifecycle.READY_NO_INIT:
            warn(f"⚠️  {agent} CLI didn't show ready marker in 60s; "
                 f"identity init prompt skipped")
            print(f"  → {agent} ({cli}) spawned (no init)")
        else:  # READY
            print(f"  → {agent} ({cli}) spawned")

    print(f"✅ team {session} started ({len(agent_list)} agents)")
    return 0
