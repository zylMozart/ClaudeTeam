"""`claudeteam start`

Bring up the whole team described in team.json: one tmux session, one
window per agent, each running its configured CLI.
"""
from __future__ import annotations

from claudeteam.runtime import config, lifecycle, tmux
from claudeteam.store import local_facts
from claudeteam.util import error_exit, maybe_print_help, warn


def main(argv: list[str]) -> int:
    if maybe_print_help(argv, "usage: claudeteam start"):
        return 0

    team = config.load_team()
    agents = team.get("agents", {})
    if not agents:
        return error_exit("❌ claudeteam.toml has no agents")

    session = team.get("session", "ClaudeTeam")
    agent_list = sorted(agents)
    first = agent_list[0]

    # Headless mode: no tmux on this host (Windows native, or a minimal
    # server). ACP agents don't need it — their CLIs live inside the
    # router; the pane is only a cosmetic viewer. Provision the ACP part
    # of the roster and refuse only the pane-bound agents, loudly.
    if not tmux.available():
        acp = [a for a in agent_list if config.agent_runner(a) == "acp"]
        pane_bound = [a for a in agent_list if a not in acp]
        if not acp:
            return error_exit(
                "❌ tmux not found and no agent uses the acp runner — "
                "install tmux, or move agents to ACP-capable CLIs "
                "(claude-code / codex-cli)")
        print(f"🕶  headless mode: tmux not found — {len(acp)} acp agent(s) "
              f"run inside the router, no viewer panes")
        if pane_bound:
            warn(f"⚠️  skipping tmux-runner agent(s) {', '.join(pane_bound)} "
                 f"— they need tmux (install it, or switch their cli)")
        for agent in acp:
            if local_facts.is_retired(agent):
                print(f"  ⏸️  {agent} 已停止 (fired); skipping")
                continue
            lifecycle.provision_headless(agent)
            cli = agents.get(agent, {}).get("cli", "claude-code")
            print(f"  → {agent} ({cli}) ready (acp, headless)")
        return 0

    if tmux.has_session(session):
        print(f"⚠️  session {session} already running; refusing to start over")
        return 1

    if not tmux.new_session(session, window=first):
        return error_exit(f"❌ failed to create tmux session {session}")
    print(f"🚀 created tmux session {session} (initial window: {first})")

    skipped_fired = 0
    provisioned = 0
    for agent in agent_list:
        # Retirement gate: a fired agent (status 已停止) is NOT re-provisioned
        # by a mass `start`/`up`. Firing is an authoritative "stay down"
        # marker; reviving the whole roster blind used to resurrect agents
        # the boss had deliberately stopped (裁员不彻底). Deliberate
        # bring-back is `claudeteam hire <agent>`, which clears the row.
        if local_facts.is_retired(agent):
            print(f"  ⏸️  {agent} 已停止 (fired); skipping "
                  f"(use `claudeteam hire {agent}` to bring back)")
            skipped_fired += 1
            continue
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
        provisioned += 1
        if outcome == lifecycle.LAZY:
            print(f"  → {agent} ({cli}) lazy-pane ready")
        elif outcome == lifecycle.SPAWN_FAILED:
            warn(f"⚠️  failed to spawn CLI in {agent} pane")
        elif outcome == lifecycle.CONFIG_ERROR:
            warn(f"⚠️  {agent} skipped: bad cli config in claudeteam.toml")
        elif outcome == lifecycle.READY_NO_INIT:
            warn(f"⚠️  {agent} CLI didn't show ready marker in 60s; "
                 f"identity init prompt skipped")
            print(f"  → {agent} ({cli}) spawned (no init)")
        else:  # READY
            print(f"  → {agent} ({cli}) spawned")

    tail = f" ({skipped_fired} fired, skipped)" if skipped_fired else ""
    print(f"✅ team {session} started ({provisioned} agents){tail}")
    # The crew "reports in" via the manager-driven roll-call that `up` kicks off
    # once the router is live (see up._summon_roster) — not a system-posted card.
    return 0
