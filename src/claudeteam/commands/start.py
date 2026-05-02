"""`claudeteam start`

Bring up the whole team described in team.json: one tmux session, one
window per agent, each running its configured CLI.
"""
from __future__ import annotations

from pathlib import Path

from claudeteam.agents import adapter_for_agent, identity
from claudeteam.agents.codex_cli import ensure_workdir_trusted
from claudeteam.runtime import config, tmux
from claudeteam.store import local_facts
from claudeteam.util import error_exit, help_requested, warn


def main(argv: list[str]) -> int:
    if help_requested(argv):
        print("usage: claudeteam start")
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
        if agent != first:
            if not tmux.new_window(target):
                warn(f"⚠️  failed to create window for {agent}, skipping")
                continue
        cfg = config.agent_config(agent)
        cli = cfg.get("cli", "claude-code")
        identity.write(agent)
        if cfg.get("lazy"):
            local_facts.upsert_status(agent, "待命", "lazy: CLI starts on first message")
            print(f"  → {agent} ({cli}) lazy-pane ready")
            continue
        if cli == "codex-cli":
            ensure_workdir_trusted(Path.cwd())
        adapter = adapter_for_agent(agent)
        cmd = adapter.spawn_cmd(agent, config.agent_model(agent))
        if not tmux.spawn_agent(target, cmd):
            warn(f"⚠️  failed to spawn CLI in {agent} pane")
            continue
        local_facts.upsert_status(agent, "进行中", "initializing")
        print(f"  → {agent} ({cli}) spawned")

    print(f"✅ team {session} started ({len(agent_list)} agents)")
    return 0
