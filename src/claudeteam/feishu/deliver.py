"""Apply a router Decision: write inbox rows + (best-effort) inject panes.

Separated from `router.classify_event` so the routing decision stays a
pure function and the side-effecting "apply" step is the only place that
touches the store and tmux.

`apply` branches on `decision.action`:

  DROP       no-op (`DeliveryReport(skipped=True)`)
  SLASH      `_apply_slash`: dispatch via `feishu/slash.dispatch` →
             reply is `str` or `dict` (R79 cards). dict → `chat.send_card`,
             str → `chat.send_text`. Pane never touched, no LLM runs.
  BROADCAST  same as ROUTE but targets are all non-sender agents
  ROUTE      per-target: `_write_inbox` (always; flock-serialised) +
             `_inject_to_pane` (best-effort; skipped when `wake.is_rate_limited`
             returns True so the inbox row stays the canonical record).

Returns a `DeliveryReport` so callers can log / surface partial-success
without inspecting hand-rolled tuples. Lists in the report:
  written / injected / failed_inject / rate_limited (per agent),
  skipped (DROP), slash_reply (SLASH text-form replies only).
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Callable

from claudeteam.agents import adapter_for_agent as _default_adapter_for_agent
from claudeteam.agents import identity as _identity
from claudeteam.feishu import chat as _chat
from claudeteam.feishu import slash as _slash
from claudeteam.feishu.router import Action, Decision
from claudeteam.runtime import config, tmux, wake
from claudeteam.runtime.lifecycle import pane_env_prefix
from claudeteam.store import local_facts


@dataclass
class DeliveryReport:
    written: list[str] = field(default_factory=list)        # inbox row landed
    injected: list[str] = field(default_factory=list)       # pane received text
    failed_inject: list[str] = field(default_factory=list)
    rate_limited: list[str] = field(default_factory=list)   # inbox kept, inject skipped
    skipped: bool = False                                    # True iff decision was DROP
    slash_reply: str = ""                                    # set when action=SLASH


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


def _build_wake_args(agent: str, adapter) -> dict:
    """Kwargs for wake_fn: spawn_cmd, init_msg, on_woken.

    Wrapping the lazy-wake setup keeps `_inject_to_pane` focused on its
    actual job (deliver text) and isolates the cross-module wiring
    (lifecycle.pane_env_prefix, identity.init_prompt, status upsert).
    """
    spawn_cmd = f"{pane_env_prefix()} {adapter.spawn_cmd(agent, config.agent_model(agent))}"
    return {
        "spawn_cmd": spawn_cmd,
        "init_msg": _identity.init_prompt(agent),
        # Flip status from "待命" to "进行中" so `claudeteam team` reflects
        # reality once the lazy pane actually wakes up.
        "on_woken": lambda: local_facts.upsert_status(
            agent, "进行中", "responding to first message"),
    }


def _inject_to_pane(agent: str, decision: Decision,
                    deps: _Deps, wake_fn: Callable | None) -> str:
    """Deliver `decision.text` to the agent's pane.

    Returns a DeliveryReport field name: 'injected' / 'failed_inject' /
    'rate_limited'.
    """
    target = tmux.Target(deps.session, agent)
    try:
        adapter = deps.adapter_for_agent(agent)
        if wake.is_rate_limited(target, adapter):
            print(f"  ⏸  {agent} rate-limited; inbox row kept, inject skipped")
            return "rate_limited"
        # R149: pre-check is_ready so we can skip _build_wake_args when
        # the pane is already awake (the common case after first wake).
        # `**_build_wake_args(...)` is evaluated at call time and pulls
        # `config.agent_model` (disk read) + `identity.init_prompt`
        # (memory file read) — both wasted when wake_fn would just
        # early-return True. wake_if_dormant still does its own is_ready
        # check internally, which is a redundant capture only on the
        # cold path; the happy path saves the two reads.
        if wake_fn is not None and not wake.is_ready(target, adapter):
            if not wake_fn(target, adapter, **_build_wake_args(agent, adapter)):
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
          session: str | None = None,
          team_agents: list[str] | None = None,
          slash_dispatch: Callable | None = None,
          chat_send: Callable | None = None,
          chat_send_card: Callable | None = None,
          chat_id: str | None = None,
          profile: str | None = None) -> DeliveryReport:
    """Apply `decision`. Side-effects per action:

    DROP       — no-op (skipped=True).
    SLASH      — dispatch via slash registry, post reply to chat as bot.
                 Zero pane touches.
    BROADCAST  — same as ROUTE but targets are all non-sender agents.
    ROUTE      — write inbox row + tmux inject for each target.

    All collaborators are injectable for tests; production defaults read
    from the real modules.
    """
    if decision.is_drop():
        return DeliveryReport(skipped=True)

    deps = _resolve_deps(adapter_for_agent, tmux_inject, append_message, session)

    if decision.action is Action.SLASH:
        return _apply_slash(decision, deps,
                            team_agents=team_agents,
                            slash_dispatch=slash_dispatch,
                            chat_send=chat_send,
                            chat_send_card=chat_send_card,
                            chat_id=chat_id,
                            profile=profile)

    sender = decision.sender or "user"
    report = DeliveryReport()
    for agent in decision.targets:
        if not _write_inbox(agent, sender, decision, deps, report):
            continue
        outcome = _inject_to_pane(agent, decision, deps, wake_fn)
        getattr(report, outcome).append(agent)
    return report


def _apply_slash(decision: Decision, deps: _Deps, *,
                 team_agents: list[str] | None,
                 slash_dispatch: Callable | None,
                 chat_send: Callable | None,
                 chat_send_card: Callable | None,
                 chat_id: str | None,
                 profile: str | None) -> DeliveryReport:
    """Run slash command at router level (zero LLM) and post reply to chat
    as bot. Pane is never touched.

    Round-79: dispatch may now return a dict (Feishu card schema) — branch
    on type to call chat.send_card instead of chat.send_text. `reply_to`
    only applies to the text path; cards don't support thread-reply.
    """
    dispatch = slash_dispatch or _slash.dispatch
    ctx = _slash.SlashContext(
        team_agents=team_agents or config.agent_names(),
        session=deps.session,
    )
    reply = dispatch(decision.text, ctx)

    report = DeliveryReport(slash_reply=reply if isinstance(reply, str) else "")
    chat = chat_id if chat_id is not None else config.chat_id()
    if not chat:
        preview = (reply[:200] if isinstance(reply, str)
                   else str(reply)[:200])
        print(f"  ⚠️ slash reply ready but chat_id unset; reply suppressed:\n{preview}")
        return report
    prof = profile if profile is not None else config.lark_profile()
    if isinstance(reply, dict):
        send_card = chat_send_card or _chat.send_card
        result = send_card(chat, reply, profile=prof, as_user=False)
    else:
        send_text = chat_send or _chat.send_text
        result = send_text(chat, reply, profile=prof, as_user=False,
                           reply_to=decision.msg_id)
    if result is None:
        # chat.send_text/send_card already logged the underlying failure.
        # Surface a one-line warning here so router.log makes it obvious
        # the slash dispatch ran but the reply never landed in chat.
        print(f"  ⚠️ slash dispatched OK but chat reply for {decision.msg_id} failed to post")
    return report
