"""`claudeteam hire <agent>`

Add a single agent to a running team: create the tmux window, spawn its
CLI, mark status.  Errors out if the team isn't running yet (use
`claudeteam start` first).
"""
from __future__ import annotations

from claudeteam.agents import adapter_for_agent, identity
from claudeteam.runtime import config, tmux
from claudeteam.store import local_facts
from claudeteam.util import error_exit, usage_error


USAGE = "usage: claudeteam hire <agent>"


def main(argv: list[str]) -> int:
    if len(argv) < 1:
        return usage_error(USAGE)
    agent = argv[0]

    try:
        cfg = config.agent_config(agent)
    except KeyError:
        return error_exit(f"❌ unknown agent: {agent} (not in team.json)")

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

    identity.write(agent)
    if cfg.get("lazy"):
        local_facts.upsert_status(agent, "待命", "lazy: CLI starts on first message")
        print(f"✅ hired (lazy): {agent} ({config.agent_cli(agent)}) → {target}")
        return 0

    adapter = adapter_for_agent(agent)
    cmd = adapter.spawn_cmd(agent, config.agent_model(agent))
    if not tmux.spawn_agent(target, cmd):
        return error_exit(f"❌ failed to spawn CLI in {agent} pane")

    local_facts.upsert_status(agent, "进行中", "initializing")
    print(f"✅ hired: {agent} ({config.agent_cli(agent)}) → {target}")
    return 0
