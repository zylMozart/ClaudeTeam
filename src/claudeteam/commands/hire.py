"""`claudeteam hire <agent>`

Add a single agent to a running team: create the tmux window, spawn its
CLI, mark status.  Errors out if the team isn't running yet (use
`claudeteam start` first).
"""
from __future__ import annotations

import sys

from claudeteam.agents import adapter_for_agent, identity
from claudeteam.runtime import config, tmux
from claudeteam.store import local_facts
from claudeteam.util import usage_error


USAGE = "usage: claudeteam hire <agent>"


def main(argv: list[str]) -> int:
    if len(argv) < 1:
        return usage_error(USAGE)
    agent = argv[0]

    try:
        config.agent_config(agent)
    except KeyError:
        print(f"❌ unknown agent: {agent} (not in team.json)", file=sys.stderr)
        return 1

    session = config.session_name()
    if not tmux.has_session(session):
        print(f"❌ tmux session {session} not running; run `claudeteam start` first",
              file=sys.stderr)
        return 1

    target = tmux.Target(session, agent)
    if tmux.has_window(target):
        print(f"⚠️  {agent} already has a pane")
        return 0

    if not tmux.new_window(target):
        print(f"❌ failed to create window for {agent}", file=sys.stderr)
        return 1

    identity.write(agent)
    cfg = config.agent_config(agent)
    if cfg.get("lazy"):
        local_facts.upsert_status(agent, "待命", "lazy: CLI starts on first message")
        print(f"✅ hired (lazy): {agent} ({config.agent_cli(agent)}) → {target}")
        return 0

    adapter = adapter_for_agent(agent)
    cmd = adapter.spawn_cmd(agent, config.agent_model(agent))
    if not tmux.spawn_agent(target, cmd):
        print(f"❌ failed to spawn CLI in {agent} pane", file=sys.stderr)
        return 1

    local_facts.upsert_status(agent, "进行中", "initializing")
    print(f"✅ hired: {agent} ({config.agent_cli(agent)}) → {target}")
    return 0
