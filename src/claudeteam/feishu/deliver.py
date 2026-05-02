"""Apply a router Decision: write inbox rows + (best-effort) inject panes.

Separated from `router.classify_event` so the routing decision stays a
pure function and the side-effecting "apply" step is the only place that
touches the store and tmux.

Returns a `DeliveryReport` so callers can log / surface partial-success
without inspecting hand-rolled tuples.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Callable

from claudeteam.agents import adapter_for_agent as _default_adapter_for_agent
from claudeteam.feishu.router import Decision
from claudeteam.runtime import config, tmux, wake
from claudeteam.store import local_facts


@dataclass
class DeliveryReport:
    written: list[str] = field(default_factory=list)        # inbox row landed
    injected: list[str] = field(default_factory=list)       # pane received text
    failed_inject: list[str] = field(default_factory=list)
    rate_limited: list[str] = field(default_factory=list)   # inbox kept, inject skipped
    skipped: bool = False                                    # True iff decision was DROP


@dataclass(frozen=True)
class _Deps:
    adapter_for_agent: Callable
    tmux_inject: Callable
    append_message: Callable
    session: str


def _resolve_deps(adapter_lookup, tmux_inject, append_message, session) -> _Deps:
    """Fill in production defaults for any None collaborator."""
    return _Deps(
        adapter_for_agent=adapter_lookup or _default_adapter_for_agent,
        tmux_inject=tmux_inject or tmux.inject,
        append_message=append_message or local_facts.append_message,
        session=session or config.session_name(),
    )


def _write_inbox(agent: str, sender: str, decision: Decision,
                 deps: _Deps, report: DeliveryReport) -> bool:
    try:
        deps.append_message(agent, sender, decision.text)
    except Exception as e:
        print(f"  ⚠️ inbox write failed for {agent}: {e}")
        return False
    report.written.append(agent)
    return True


def _inject_to_pane(agent: str, decision: Decision,
                    deps: _Deps, wake_fn: Callable | None) -> str:
    """Try to deliver `decision.text` to the agent's pane.

    Returns a DeliveryReport field name: 'injected' / 'failed_inject' /
    'rate_limited'.
    """
    target = tmux.Target(deps.session, agent)
    try:
        adapter = deps.adapter_for_agent(agent)
        if wake.is_rate_limited(target, adapter):
            print(f"  ⏸  {agent} rate-limited; inbox row kept, inject skipped")
            return "rate_limited"
        if wake_fn is not None:
            spawn_cmd = adapter.spawn_cmd(agent, config.agent_model(agent))
            if not wake_fn(target, adapter, spawn_cmd=spawn_cmd):
                print(f"  ⚠️ {agent} pane not ready; injecting anyway")
        ok = deps.tmux_inject(target, decision.text, submit_keys=adapter.submit_keys())
    except Exception as e:
        print(f"  ⚠️ inject error for {agent}: {e}")
        return "failed_inject"
    return "injected" if ok else "failed_inject"


def apply(decision: Decision, *,
          adapter_for_agent: Callable | None = None,
          tmux_inject: Callable | None = None,
          append_message: Callable | None = None,
          wake_fn: Callable | None = None,
          session: str | None = None) -> DeliveryReport:
    """Apply `decision`. Side-effects: append_message + (optional wake) +
    tmux.inject. Skips inject when the pane is rate-limited.

    All collaborators are injectable for tests; production defaults read
    from the real modules.
    """
    if decision.is_drop():
        return DeliveryReport(skipped=True)

    deps = _resolve_deps(adapter_for_agent, tmux_inject, append_message, session)
    sender = decision.sender or "user"
    report = DeliveryReport()
    for agent in decision.targets:
        if not _write_inbox(agent, sender, decision, deps, report):
            continue
        outcome = _inject_to_pane(agent, decision, deps, wake_fn)
        getattr(report, outcome).append(agent)
    return report
