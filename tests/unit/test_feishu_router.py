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


# ── ROUTE: @-mentions ─────────────────────────────────────────────


def test_human_at_mention_routes_to_mentioned_agent():
    d = classify_event(_ev(text="@worker_codex review this"), team_agents=_AGENTS)
    assert d.action is Action.ROUTE
    assert d.targets == ["worker_codex"]
    assert d.sender == ""  # human


def test_multiple_mentions_preserve_order_and_dedupe():
    d = classify_event(
        _ev(text="@worker_cc and @worker_codex and @worker_cc"),
        team_agents=_AGENTS,
    )
    assert d.targets == ["worker_cc", "worker_codex"]


def test_mentions_of_unknown_names_are_ignored_falling_to_default():
    d = classify_event(_ev(text="hey @stranger please help"), team_agents=_AGENTS)
    assert d.targets == ["manager"]  # only @-mentions matching team count


def test_agent_at_mention_excludes_sender_from_targets():
    """`[worker_cc] @worker_cc reminding myself` shouldn't loop the sender."""
    d = classify_event(
        _ev(text="[worker_cc] @worker_cc and @worker_codex"),
        team_agents=_AGENTS,
    )
    assert d.sender == "worker_cc"
    assert d.targets == ["worker_codex"]


# ── ROUTE: agent-tagged sender ────────────────────────────────────


def test_agent_prefix_is_stripped_from_text():
    d = classify_event(
        _ev(text="[worker_codex] @manager status update"),
        team_agents=_AGENTS,
    )
    assert d.sender == "worker_codex"
    assert d.targets == ["manager"]
    assert d.text.startswith("@manager status update")


def test_agent_prefix_with_unknown_name_does_not_match():
    """`[stranger] hi` is treated as plain text from a human."""
    d = classify_event(_ev(text="[stranger] hi"), team_agents=_AGENTS)
    assert d.sender == ""
    assert d.targets == ["manager"]
    assert d.text == "[stranger] hi"


def test_at_mention_alternative_prefix_form():
    d = classify_event(_ev(text="@worker_cc: do the thing"), team_agents=_AGENTS)
    # `@worker_cc:` matched as sender (test our regex's prefix flexibility)
    # or as a mention — verify result regardless
    assert d.action is Action.ROUTE
    assert "worker_cc" in d.targets or d.sender == "worker_cc"


# ── seen_msg_ids interaction ─────────────────────────────────────


def test_classify_does_not_mutate_seen_set():
    seen = set()
    classify_event(_ev(), team_agents=_AGENTS, seen_msg_ids=seen)
    # classifier reads but does not mutate; caller is responsible for adding
    assert seen == set()


def test_msg_id_propagates_into_decision():
    d = classify_event(_ev(message_id="om_42"), team_agents=_AGENTS)
    assert d.msg_id == "om_42"
