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


def _reidentify_one(agent: str, session: str, *,
                    cli: str = "") -> bool:
    """Inject init prompt into one pane. Returns True on success.

    Per-agent failures (no pane, inject failed, unknown adapter) print
    a one-line warning and return False so the caller can tally and
    decide overall rc.

    Optional `cli` arg lets the --all caller resolve adapters from
    a pre-loaded team config instead of paying one disk read per
    agent inside `adapter_for_agent`. Empty `cli` falls back to the
    config-driven lookup so the single-agent error contract stays.
    """
    target = tmux.Target(session, agent)
    if not tmux.has_window(target):
        print(f"  ⏭  {agent}: no pane in session {session}")
        return False
    try:
        if cli:
            from claudeteam.agents import get_adapter
            adapter = get_adapter(cli)
        else:
            adapter = adapter_for_agent(agent)
    except KeyError as e:
        print(f"  ⚠️ {agent}: {e}")
        return False
    # Re-render identity.md from current config BEFORE injecting the
    # wake prompt — the prompt only tells the pane "go read your
    # identity.md", so a stale disk file means the LLM picks up the
    # OLD specialty / role / notes. Edits to claudeteam.toml only
    # land in the pane via this rewrite.
    try:
        identity.write(agent)
    except Exception as e:
        print(f"  ⚠️ {agent}: identity write failed: {e}")
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

    # Argv validation first so existing single-agent error contracts
    # hold: `reidentify` (no arg) → usage_error;
    # `reidentify ghost` → unknown agent. The --all path skips that
    # validation but still loads team config (pre-loaded once so
    # per-agent adapter resolution doesn't re-read it N times).
    # Single-agent path keeps the config-driven lookup so the error
    # contract stays unchanged.
    agents_dict: dict = {}
    if do_all:
        agents_dict = config.load_team().get("agents", {})
        if not agents_dict:
            return error_exit("❌ team.json has no agents")
        agents = sorted(agents_dict)
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
        ok = sum(1 for a in agents
                 if _reidentify_one(
                     a, session,
                     cli=agents_dict.get(a, {}).get("cli", "claude-code")))
        print(f"reidentified {ok}/{len(agents)} agents")
        return 0 if ok == len(agents) else 1

    # Single-agent path: keeps the explicit error rendering that
    # existing tests pin against; --all is the bulk variant.
    agent = agents[0]
    target = tmux.Target(session, agent)
    if not tmux.has_window(target):
        return error_exit(
            f"❌ {agent} has no pane in session {session} "
            f"(was it fired? try `claudeteam hire {agent}`)")
    adapter = adapter_for_agent(agent)
    # Same as the --all path: re-render disk before inject so LLM
    # picks up new claudeteam.toml fields, not the snapshot at spawn.
    try:
        identity.write(agent)
    except Exception as e:
        return error_exit(f"❌ identity write failed for {agent}: {e}")
    if not tmux.inject(target, identity.init_prompt(agent),
                       submit_keys=adapter.submit_keys()):
        return error_exit(f"❌ failed to inject identity init into {agent}")
    print(f"✅ re-injected identity init into {agent} (pane: {target})")
    return 0
