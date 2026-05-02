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

from claudeteam.feishu.router import Action, Decision
from claudeteam.runtime import config, tmux
from claudeteam.store import local_facts


@dataclass
class DeliveryReport:
    written: list[str] = field(default_factory=list)   # agents whose inbox got the row
    injected: list[str] = field(default_factory=list)  # agents whose pane received the text
    failed_inject: list[str] = field(default_factory=list)
    skipped: bool = False                               # True iff decision was DROP


def apply(decision: Decision, *,
          adapter_for_agent: Callable | None = None,
          tmux_inject: Callable | None = None,
          append_message: Callable | None = None,
          session: str | None = None) -> DeliveryReport:
    """Apply `decision`. Side-effects: local_facts.append_message + tmux.inject.

    All collaborators are injectable for tests; production defaults read
    from the real modules.
    """
    if decision.is_drop():
        return DeliveryReport(skipped=True)

    # late binding so test fakes installed after import-time still take effect
    if adapter_for_agent is None:
        from claudeteam.agents import adapter_for_agent as adapter_for_agent
    if tmux_inject is None:
        tmux_inject = tmux.inject
    if append_message is None:
        append_message = local_facts.append_message
    if session is None:
        session = config.session_name()

    sender = decision.sender or "user"
    report = DeliveryReport()
    for agent in decision.targets:
        # 1) durable inbox row — always
        try:
            append_message(agent, sender, decision.text)
            report.written.append(agent)
        except Exception as e:
            print(f"  ⚠️ inbox write failed for {agent}: {e}")
            continue

        # 2) best-effort pane injection — only if the agent has a tmux window
        target = tmux.Target(session, agent)
        try:
            adapter = adapter_for_agent(agent)
            ok = tmux_inject(target, decision.text, submit_keys=adapter.submit_keys())
        except Exception as e:
            print(f"  ⚠️ inject error for {agent}: {e}")
            ok = False
        if ok:
            report.injected.append(agent)
        else:
            report.failed_inject.append(agent)

    return report
