"""Tests for feishu/router.py — pure event-routing decisions.

Every branch (DROP reasons + ROUTE patterns) gets its own assertion
since this is the heart of "who sees what message".
"""
from __future__ import annotations

from claudeteam.feishu.router import Action, classify_event


_AGENTS = ["manager", "worker_cc", "worker_codex"]


def _ev(**overrides) -> dict:
    base = {
        "message_id": "om_1",
        "chat_id": "oc_team",
        "sender_id": "ou_user",
        "text": "hello",
        "msg_type": "text",
    }
    base.update(overrides)
    return base


# ── DROP branches ──────────────────────────────────────────────────


def test_drop_when_message_id_missing():
    d = classify_event(_ev(message_id=""), team_agents=_AGENTS)
    assert d.is_drop() and d.reason == "no_msg_id"


def test_drop_when_already_seen():
    d = classify_event(_ev(), team_agents=_AGENTS, seen_msg_ids={"om_1"})
    assert d.is_drop() and d.reason == "dedup"


def test_drop_when_event_chat_id_does_not_match_team_chat():
    d = classify_event(_ev(chat_id="oc_other"), team_agents=_AGENTS, chat_id="oc_team")
    assert d.is_drop() and d.reason == "cross_team"


def test_no_drop_when_team_chat_filter_unset():
    d = classify_event(_ev(chat_id="oc_anything"), team_agents=_AGENTS, chat_id="")
    assert d.action is Action.ROUTE


def test_drop_when_sender_matches_bot_id():
    d = classify_event(_ev(sender_id="ou_bot"), team_agents=_AGENTS, bot_id="ou_bot")
    assert d.is_drop() and d.reason == "bot_self"


def test_drop_when_sender_type_is_app_even_without_bot_id():
    """REGRESSION: 2026-05-06 host_smoke caught manager's own ack cards
    looping back into manager inbox every router restart. Root cause:
    `commands/router.py` never passed bot_id to classify_event, so the
    `bot_id == sender_id` check never fired. Modern lark-cli `--compact`
    payload carries sender_type=app for bot-sent messages, and
    chat-messages-list returns id_type=app_id — both surface as
    sender_type here. R174 bot-self path now triggers on either signal.
    """
    d = classify_event(
        _ev(sender_id="cli_xxx", sender_type="app",
            text='<card title="🎯 manager">ack</card>'),
        team_agents=_AGENTS,
    )
    assert d.is_drop() and d.reason == "bot_self"


def test_drop_when_sender_id_type_is_app_id_from_catchup_path():
    """chat-messages-list shape: sender.id_type='app_id' surfaces as
    sender_type='app_id' after _msg_to_event_line + _normalise. Same
    drop path as live `app` value."""
    d = classify_event(
        _ev(sender_id="cli_xxx", sender_type="app_id",
            text='<card title="🎯 manager">ack</card>'),
        team_agents=_AGENTS,
    )
    assert d.is_drop() and d.reason == "bot_self"


def test_route_to_manager_when_worker_card_is_bot_sent():
    """R174 exception still works under the new sender_type detection:
    worker-sent cards (bot identity, but card title parses as worker_X)
    route back to manager's inbox. Real card title shape includes ` · `
    after the agent name, which `_card_sender_agent` keys on."""
    d = classify_event(
        _ev(sender_id="cli_xxx", sender_type="app",
            text='<card title="💎 worker_cc · 内容策划">完工</card>'),
        team_agents=_AGENTS,
    )
    assert d.action is Action.ROUTE
    assert d.targets == ["manager"]
    assert d.sender == "worker_cc"


def test_user_sender_type_does_not_trigger_bot_self():
    """sender_type='user' — the human path. Must still route to manager."""
    d = classify_event(
        _ev(sender_id="ou_human", sender_type="user", text="hi"),
        team_agents=_AGENTS,
    )
    assert d.action is Action.ROUTE
    assert d.targets == ["manager"]


def test_drop_when_text_empty():
    d = classify_event(_ev(text=""), team_agents=_AGENTS)
    assert d.is_drop() and d.reason == "empty"


def test_drop_when_text_only_whitespace():
    d = classify_event(_ev(text="   \n  "), team_agents=_AGENTS)
    assert d.is_drop() and d.reason == "empty"


def test_drop_when_known_agent_broadcasts_with_no_target():
    """`[manager] hi everyone` from manager → no human to deliver to."""
    d = classify_event(_ev(text="[manager] hi everyone"), team_agents=_AGENTS)
    assert d.is_drop() and d.reason == "agent_no_target"
    assert d.sender == "manager"


# ── ROUTE: human → default ────────────────────────────────────────


def test_human_message_routes_to_default_target():
    d = classify_event(_ev(text="please do X"), team_agents=_AGENTS)
    assert d.action is Action.ROUTE
    assert d.targets == ["manager"]
    assert d.text == "please do X"


def test_default_target_can_be_overridden():
    d = classify_event(_ev(text="hi"), team_agents=_AGENTS, default_target="worker_cc")
    assert d.targets == ["worker_cc"]


# ── R174: ALL human routes go to manager (mentions are text, not routes) ──


def test_human_at_mention_still_routes_only_to_manager():
    """R174: `@worker_codex review this` from boss — `@worker_codex` is
    text content for manager to parse, NOT a routing instruction.
    Manager decides whether to dispatch via `claudeteam send`."""
    d = classify_event(_ev(text="@worker_codex review this"), team_agents=_AGENTS)
    assert d.action is Action.ROUTE
    assert d.targets == ["manager"]
    assert d.sender == ""  # human


def test_multiple_mentions_still_routes_only_to_manager():
    d = classify_event(
        _ev(text="@worker_cc and @worker_codex and @worker_cc"),
        team_agents=_AGENTS,
    )
    assert d.targets == ["manager"]


def test_mentions_of_unknown_names_routes_to_manager():
    d = classify_event(_ev(text="hey @stranger please help"), team_agents=_AGENTS)
    assert d.targets == ["manager"]


# ── ROUTE: agent-tagged sender ────────────────────────────────────


def test_agent_prefix_with_no_target_drops():
    """`[worker_codex] @manager status update` → still drops as
    `agent_no_target` (this is the legacy `[<agent>]` plain-text path,
    largely dead since `claudeteam say` posts cards now). The card
    path goes through the bot_id branch instead."""
    d = classify_event(
        _ev(text="[worker_codex] @manager status update"),
        team_agents=_AGENTS,
    )
    assert d.sender == "worker_codex"
    assert d.action is Action.DROP
    assert d.reason == "agent_no_target"


def test_agent_prefix_with_unknown_name_routes_to_manager():
    """`[stranger] hi` is treated as plain text from a human → manager."""
    d = classify_event(_ev(text="[stranger] hi"), team_agents=_AGENTS)
    assert d.sender == ""
    assert d.targets == ["manager"]
    assert d.text == "[stranger] hi"


# ── seen_msg_ids interaction ─────────────────────────────────────


def test_classify_does_not_mutate_seen_set():
    seen = set()
    classify_event(_ev(), team_agents=_AGENTS, seen_msg_ids=seen)
    # classifier reads but does not mutate; caller is responsible for adding
    assert seen == set()


def test_msg_id_propagates_into_decision():
    d = classify_event(_ev(message_id="om_42"), team_agents=_AGENTS)
    assert d.msg_id == "om_42"


# ── Action.SLASH (router-level dispatch) ─────────────────────────


def test_slash_command_returns_slash_action():
    """REGRESSION (round A.2): /team etc. must NOT route as ROUTE
    (would inject into manager pane); must be SLASH for router-level
    zero-LLM dispatch."""
    d = classify_event(_ev(text="/team"), team_agents=_AGENTS)
    assert d.action is Action.SLASH
    assert d.text == "/team"
    assert d.targets == []  # SLASH never has targets — handled at router


def test_slash_with_args_keeps_full_text():
    d = classify_event(_ev(text="/tmux worker_cc 30"), team_agents=_AGENTS)
    assert d.action is Action.SLASH
    assert d.text == "/tmux worker_cc 30"


def test_slash_unknown_command_still_emits_slash():
    """Even /bogus is SLASH-classified — let dispatch's fallback handle it."""
    d = classify_event(_ev(text="/bogus arg"), team_agents=_AGENTS)
    assert d.action is Action.SLASH


def test_slash_strips_sender_prefix_before_detection():
    """REGRESSION (round A2 B1): \`claudeteam say boss "/team"\` produces
    \`[boss] /team\` in chat. Without prefix-strip, slash detection
    misses it and the message gets routed to manager (which then has
    its LLM cobble together a fake response). The prefix must be
    stripped BEFORE the / check, AND propagated as decision.text so the
    handler receives just \`/team\`."""
    d = classify_event(_ev(text="[boss] /team"), team_agents=_AGENTS)
    assert d.action is Action.SLASH
    assert d.text == "/team"


def test_slash_strips_known_agent_prefix_too():
    """If manager echoes a slash command (silly but possible), still
    SLASH — operator-style invocation regardless of prefix identity."""
    d = classify_event(_ev(text="[manager] /health"), team_agents=_AGENTS)
    assert d.action is Action.SLASH
    assert d.text == "/health"


# ── Action.BROADCAST (whole-team routing) ────────────────────────


def test_chinese_broadcast_phrase_routes_to_manager_only():
    """R174: `全体成员请汇报状态` from boss → only manager. Manager
    parses the broadcast intent and dispatches to workers."""
    d = classify_event(_ev(text="全体成员请汇报状态"), team_agents=_AGENTS)
    assert d.action is Action.ROUTE
    assert d.targets == ["manager"]


def test_at_team_token_routes_to_manager_only():
    d = classify_event(_ev(text="@team standup in 5"), team_agents=_AGENTS)
    assert d.action is Action.ROUTE
    assert d.targets == ["manager"]


def test_at_all_token_routes_to_manager_only():
    d = classify_event(_ev(text="@all heads up"), team_agents=_AGENTS)
    assert d.action is Action.ROUTE
    assert d.targets == ["manager"]


def test_at_everyone_routes_to_manager_only():
    d = classify_event(_ev(text="@everyone deploy now"), team_agents=_AGENTS)
    assert d.action is Action.ROUTE
    assert d.targets == ["manager"]


def test_action_broadcast_no_longer_emitted():
    """R174: routing-level broadcast is dead. Every variant of
    'broadcast trigger' from a human now ROUTEs to manager. The
    BROADCAST action itself is kept in the enum for legacy reasons
    but is unreachable from classify_event."""
    for text in ("全体注意", "@team x", "@all y", "@everyone z"):
        d = classify_event(_ev(text=text, message_id=f"om_{hash(text)}"),
                            team_agents=_AGENTS)
        assert d.action is Action.ROUTE, f"{text!r} should route to manager"
        assert d.targets == ["manager"], f"{text!r} target wrong"


def test_explicit_mention_with_broadcast_token_routes_to_manager():
    """`@worker_cc 全体成员都开会` — both tokens present. R174: still
    just manager. Manager reads the text and decides intent."""
    d = classify_event(_ev(text="@worker_cc 全体成员都开会"), team_agents=_AGENTS)
    assert d.action is Action.ROUTE
    assert d.targets == ["manager"]


# ── R174: bot-sent worker cards route to manager ────────────────


def test_worker_card_say_routes_to_manager_inbox():
    """Worker `claudeteam say` posts an interactive card with title
    `💎 worker_cc · ...`. sender_id == bot_id but the card-title
    parser identifies the originating agent. R174: route to manager
    so manager has visibility into worker chat replies."""
    card_text = '<card title="💎 worker_cc · 工程师">step 1 done</card>'
    d = classify_event(
        _ev(text=card_text, sender_id="bot_xxx"),
        team_agents=_AGENTS, bot_id="bot_xxx",
    )
    assert d.action is Action.ROUTE
    assert d.targets == ["manager"]
    assert d.sender == "worker_cc"  # parsed from card title


def test_manager_own_card_say_still_drops_no_self_loop():
    """Manager's own card → drop (no self-route, otherwise infinite
    loop: manager say → manager inbox → manager re-acts → say again)."""
    card_text = '<card title="🎯 manager · 团队主管">summary line</card>'
    d = classify_event(
        _ev(text=card_text, sender_id="bot_xxx"),
        team_agents=_AGENTS, bot_id="bot_xxx",
    )
    assert d.action is Action.DROP
    assert d.reason == "bot_self"


def test_unparseable_bot_message_drops():
    """Bot sends some plain-text reply that isn't card-shaped — no
    agent attribution possible — drop as before."""
    d = classify_event(
        _ev(text="some bot reply", sender_id="bot_xxx"),
        team_agents=_AGENTS, bot_id="bot_xxx",
    )
    assert d.action is Action.DROP
    assert d.reason == "bot_self"


def test_worker_card_with_chinese_role_still_parsed():
    """The role text in the title is Chinese (`container-A 工程师`),
    but the agent name `worker_cc` is what the parser keys on."""
    card_text = '<card title="💎 worker_cc · container-A 工程师">报道</card>'
    d = classify_event(
        _ev(text=card_text, sender_id="bot_xxx"),
        team_agents=_AGENTS, bot_id="bot_xxx",
    )
    assert d.action is Action.ROUTE
    assert d.targets == ["manager"]
    assert d.sender == "worker_cc"
