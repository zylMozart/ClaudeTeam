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
                 deps: _Deps, report: DeliveryReport) -> str:
    """Returns the local_id on success, "" on failure (failure is also
    logged to the report). R172.b: caller threads the local_id into
    the pane-inject wrapper so the agent knows which row to mark
    `claudeteam read` after replying."""
    try:
        local_id = deps.append_message(agent, sender, decision.text)
    except Exception as e:
        print(f"  ⚠️ inbox write failed for {agent}: {e}")
        return ""
    report.written.append(agent)
    return local_id or ""


def _build_wake_args(agent: str, adapter) -> dict:
    """Kwargs for wake_fn: spawn_cmd, init_msg, on_woken.

    Wrapping the lazy-wake setup keeps `_inject_to_pane` focused on its
    actual job (deliver text) and isolates the cross-module wiring
    (lifecycle.pane_env_prefix, identity.init_prompt, status upsert).
    """
    from claudeteam.runtime import tunables
    spawn_cmd = f"{pane_env_prefix()} {adapter.spawn_cmd(agent, config.agent_model(agent))}"
    return {
        "spawn_cmd": spawn_cmd,
        "init_msg": _identity.init_prompt(agent),
        "timeout_s": float(tunables.tunable("wake.lazy_wake_timeout_s", 30.0)),
        # Flip status from "待命" to "进行中" so `claudeteam team` reflects
        # reality once the lazy pane actually wakes up.
        "on_woken": lambda: local_facts.upsert_status(
            agent, "进行中", "responding to first message"),
    }


# Heuristic: if the boss message asks for a summary / report-back / status
# coordinated through manager, workers should also send the result to
# manager (not just `say` to chat) so manager's inbox pings and they can
# follow up. manager's pane doesn't see chat messages — only its own
# inbox + dispatched messages — so without this hint the dispatch +
# summarize loop stalls (boss saw this 2026-05-05 in a Round C dry-run:
# manager dispatched, worker counted, posted to chat, manager never
# learned and never summarized).
_SUMMARY_CUE_TOKENS = (
    "汇总", "汇报", "总结", "报告",
    "summarize", "summary", "report back",
    "manager 跟进", "manager 综合",
)


def _wants_manager_summary(text: str) -> bool:
    low = text.lower()
    return any(tok.lower() in low for tok in _SUMMARY_CUE_TOKENS)


def _compose_inject_text(agent: str, decision: Decision,
                         local_id: str = "") -> str:
    """Prepend a short routing-context header to the chat message before
    injecting it into the agent's pane.

    R172.b: claude in the pane treats raw injected text as a regular
    user prompt and answers IN PANE. The hint primes the agent to:
      1. Reply via the correct channel (`claudeteam say` for chat-
         originated; `claudeteam send` for peer messages).
      2. Mark the inbox row `read` afterward (deliver knows the
         local_id since it just appended the row) — keeps the inbox
         from accumulating unread rows.
      3. R173: if the message hints at manager-summary follow-up,
         non-manager agents are told to ALSO `claudeteam send manager`
         so manager's inbox pings (manager pane is blind to chat-only
         say events). Without this, Round C dispatch + summarize loops
         stall after worker posts result."""
    sender = decision.sender or "user"
    read_hint = (f" 完成后用 `claudeteam read {local_id}` 销 inbox。"
                 if local_id else "")
    summary_hint = ""
    if (agent != "manager"
            and _wants_manager_summary(decision.text)):
        summary_hint = (f" 这条似乎需要 manager 汇总，处理完后**额外**"
                        f"发一句 `claudeteam send manager {agent} \"<结果>\"` "
                        f"让 manager inbox 知道你的进度。")
    # 简短引导 — 长解释属于 identity.md 的职责，不是每次注入都重复一遍。
    # 关键指示：哪个频道回 + 怎么 mark read（如果 local_id 已知）+ 是否需
    # 要 send manager 让其汇总。具体命令格式 / --to 选择交给 identity 教。
    if sender == "user" or not sender:
        hint = (f"[群聊·老板] 用 `claudeteam say {agent} \"...\" --to user` "
                f"回群。{summary_hint}{read_hint}")
    else:
        hint = (f"[同事·{sender}] 回 `claudeteam send {sender} {agent} "
                f"\"...\"`；要公告到群用 `claudeteam say {agent} "
                f"\"...\" --to user`。{read_hint}")
    return f"{hint}\n\n{decision.text}"


def _inject_to_pane(agent: str, decision: Decision,
                    deps: _Deps, wake_fn: Callable | None,
                    local_id: str = "") -> str:
    """Deliver `decision.text` to the agent's pane (wrapped with a
    routing-context hint so the agent posts replies via `claudeteam
    say` instead of answering in pane). `local_id` is appended to the
    hint so the agent knows which inbox row to mark read.

    Returns a DeliveryReport field name: 'injected' / 'failed_inject' /
    'rate_limited'.
    """
    target = tmux.Target(deps.session, agent)
    try:
        adapter = deps.adapter_for_agent(agent)
        if wake.is_rate_limited(target, adapter):
            print(f"  ⏸  {agent} rate-limited; inbox row kept, inject skipped")
            return "rate_limited"
        if wake_fn is not None and not wake.is_ready(target, adapter):
            if not wake_fn(target, adapter, **_build_wake_args(agent, adapter)):
                print(f"  ⚠️ {agent} pane not ready; injecting anyway")
        text = _compose_inject_text(agent, decision, local_id=local_id)
        ok = deps.tmux_inject(target, text, submit_keys=adapter.submit_keys())
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
          lazy_agents: frozenset[str] | None = None,
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
                            lazy_agents=lazy_agents,
                            slash_dispatch=slash_dispatch,
                            chat_send=chat_send,
                            chat_send_card=chat_send_card,
                            chat_id=chat_id,
                            profile=profile)

    sender = decision.sender or "user"
    report = DeliveryReport()
    for agent in decision.targets:
        local_id = _write_inbox(agent, sender, decision, deps, report)
        if not local_id:
            continue
        outcome = _inject_to_pane(agent, decision, deps, wake_fn,
                                   local_id=local_id)
        getattr(report, outcome).append(agent)
    return report


def _apply_slash(decision: Decision, deps: _Deps, *,
                 team_agents: list[str] | None,
                 lazy_agents: frozenset[str] | None,
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
        lazy_agents=lazy_agents if lazy_agents is not None else frozenset(),
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
