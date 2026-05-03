"""`claudeteam reidentify <agent>` — single agent
`claudeteam reidentify --all`   — every agent in team.json with a live pane

Re-inject the identity init prompt into running agents' panes. Useful
when an agent has just `/compact`'d its context and forgot who it is, or
when an operator just edited `team.json` / identity templates and wants
the team to pick up the changes without `down` + `up`.

Does NOT spawn a new pane or restart the CLI — only sends the init
prompt as a fresh user message. The agent re-reads `identity.md` and
re-introduces itself in chat.

Round-91: `--all` flag added. Skips agents that don't have a live
pane (lazy / fired) and prints one line per agent for visibility.
Returns rc=0 only if every targeted agent re-injected successfully.
"""
from __future__ import annotations

from claudeteam.agents import adapter_for_agent, identity
from claudeteam.runtime import config, tmux
from claudeteam.util import error_exit, pop_bool_flag, usage_error


USAGE = "usage: claudeteam reidentify <agent>  |  claudeteam reidentify --all"


def _reidentify_one(agent: str, session: str) -> bool:
    """Inject init prompt into one pane. Returns True on success.

    Per-agent failures (no pane, inject failed, unknown adapter) print
    a one-line warning and return False so the caller can tally and
    decide overall rc.
    """
    target = tmux.Target(session, agent)
    if not tmux.has_window(target):
        print(f"  ⏭  {agent}: no pane in session {session}")
        return False
    try:
        adapter = adapter_for_agent(agent)
    except KeyError as e:
        print(f"  ⚠️ {agent}: {e}")
        return False
    if not tmux.inject(target, identity.init_prompt(agent),
                       submit_keys=adapter.submit_keys()):
        print(f"  ❌ {agent}: tmux inject failed")
        return False
    print(f"  ✅ {agent} (pane: {target})")
    return True


def main(argv: list[str]) -> int:
    rest = list(argv)
    do_all = pop_bool_flag(rest, "--all")

    # Argv validation first so existing single-agent error contracts hold:
    # `reidentify` (no arg) → usage_error; `reidentify ghost` → unknown agent.
    # Round-91 swap: --all skips arg validation but still hits team.json.
    if do_all:
        agents = config.agent_names()
        if not agents:
            return error_exit("❌ team.json has no agents")
    else:
        if len(rest) < 1:
            return usage_error(USAGE)
        agent = rest[0]
        try:
            config.agent_config(agent)
        except KeyError:
            return error_exit(f"❌ unknown agent: {agent} (not in team.json)")
        agents = [agent]

    session = config.session_name()
    if not tmux.has_session(session):
        return error_exit(
            f"❌ tmux session {session} not running; run `claudeteam up` first")

    if do_all:
        print(f"🔁 reidentify all ({len(agents)} agents in {session}):")
        ok = sum(1 for a in agents if _reidentify_one(a, session))
        print(f"reidentified {ok}/{len(agents)} agents")
        return 0 if ok == len(agents) else 1

    # Single-agent path: keep the explicit error rendering the existing
    # tests pin against (was the behaviour before round-91; --all is the
    # only new branch).
    agent = agents[0]
    target = tmux.Target(session, agent)
    if not tmux.has_window(target):
        return error_exit(
            f"❌ {agent} has no pane in session {session} "
            f"(was it fired? try `claudeteam hire {agent}`)")
    adapter = adapter_for_agent(agent)
    if not tmux.inject(target, identity.init_prompt(agent),
                       submit_keys=adapter.submit_keys()):
        return error_exit(f"❌ failed to inject identity init into {agent}")
    print(f"✅ re-injected identity init into {agent} (pane: {target})")
    return 0
