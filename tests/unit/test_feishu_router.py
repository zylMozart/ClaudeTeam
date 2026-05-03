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


def test_broadcast_chinese_phrase_routes_to_all_non_sender():
    d = classify_event(_ev(text="全体成员请汇报状态"), team_agents=_AGENTS)
    assert d.action is Action.BROADCAST
    assert set(d.targets) == set(_AGENTS)


def test_broadcast_at_team_token():
    d = classify_event(_ev(text="@team standup in 5"), team_agents=_AGENTS)
    assert d.action is Action.BROADCAST
    assert set(d.targets) == set(_AGENTS)


def test_broadcast_at_all_token():
    d = classify_event(_ev(text="@all heads up"), team_agents=_AGENTS)
    assert d.action is Action.BROADCAST


def test_broadcast_at_everyone_token():
    """`@everyone` is in _BROADCAST_TOKENS too — third trigger besides
    @team / @all. Was missing direct coverage."""
    d = classify_event(_ev(text="@everyone deploy now"), team_agents=_AGENTS)
    assert d.action is Action.BROADCAST


def test_broadcast_substring_does_not_match():
    """`@teammate` looks like a broadcast token but isn't — the regex
    token-boundary check (^|\\s before, \\s|$|[,!?...] after) must reject
    it, otherwise an @-mention to a worker named "teammate" would
    silently fan out to everyone. No `teammate` agent in this team, so
    the message routes as default-target instead."""
    d = classify_event(_ev(text="@teammate hi"), team_agents=_AGENTS)
    assert d.action is not Action.BROADCAST
    assert d.targets == ["manager"]


def test_broadcast_token_followed_by_punctuation_still_matches():
    """`@team!` / `@team,` / `@team?` should all match — punctuation is
    in the allowed after-set so urgent messages still trigger broadcast."""
    for trailing in ("!", ",", ".", "?"):
        text = f"@team{trailing} ASAP"
        d = classify_event(_ev(text=text, message_id=f"om_punct_{trailing}"),
                           team_agents=_AGENTS)
        assert d.action is Action.BROADCAST, (
            f"@team{trailing} should still trigger broadcast")


def test_broadcast_token_mid_word_does_not_match():
    """`team@team` (no whitespace before the token) should NOT trigger
    broadcast — otherwise email-style addresses or jargon could fire
    it accidentally."""
    d = classify_event(_ev(text="discussed at team@team yesterday"),
                       team_agents=_AGENTS)
    assert d.action is not Action.BROADCAST


def test_broadcast_excludes_agent_sender():
    """When manager broadcasts, manager itself is not a target."""
    d = classify_event(_ev(text="[manager] @team please report"),
                       team_agents=_AGENTS)
    assert d.action is Action.BROADCAST
    assert "manager" not in d.targets
    assert set(d.targets) == set(_AGENTS) - {"manager"}


def test_explicit_mention_overrides_broadcast():
    """If text has both @worker_cc and 全体, explicit mention wins."""
    d = classify_event(_ev(text="@worker_cc 全体成员都开会"), team_agents=_AGENTS)
    assert d.action is Action.ROUTE
    assert d.targets == ["worker_cc"]


def test_broadcast_token_must_be_word_boundary():
    """'@teammate' should not be misread as @team broadcast."""
    d = classify_event(_ev(text="ping @teammate later"), team_agents=_AGENTS)
    assert d.action is not Action.BROADCAST
