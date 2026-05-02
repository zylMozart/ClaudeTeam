"""Tests for feishu/subscribe.py — NDJSON event-loop processing."""
from __future__ import annotations

import json

from claudeteam.feishu.router import Decision
from claudeteam.feishu.subscribe import process_lines


_AGENTS = ["manager", "worker_cc", "worker_codex"]


def _ndjson(*events: dict) -> list[str]:
    return [json.dumps(ev) for ev in events]


def _wrapped(message_id: str, chat_id: str, sender_open_id: str,
             content_text: str, *, msg_type: str = "text") -> dict:
    """Mirror lark-cli --compact event payload shape."""
    return {
        "event": {
            "message": {
                "message_id": message_id,
                "chat_id": chat_id,
                "message_type": msg_type,
                "content": json.dumps({"text": content_text}),
            },
            "sender": {"sender_id": {"open_id": sender_open_id}},
        }
    }


def test_empty_iterable_returns_zero_stats():
    apply_calls = []
    stats = process_lines(
        iter([]),
        team_agents=_AGENTS,
        apply_fn=lambda d: apply_calls.append(d),
    )
    assert stats.handled == 0
    assert stats.dropped == 0
    assert apply_calls == []


def test_single_human_message_routes_to_manager_via_apply():
    line = _ndjson(_wrapped("om_1", "oc_team", "ou_user", "please help"))
    applied = []
    stats = process_lines(
        line,
        team_agents=_AGENTS,
        chat_id="oc_team",
        apply_fn=applied.append,
    )
    assert stats.handled == 1
    assert stats.dropped == 0
    assert len(applied) == 1
    decision = applied[0]
    assert isinstance(decision, Decision)
    assert decision.targets == ["manager"]
    assert decision.text == "please help"


def test_dedup_drops_repeated_message_ids():
    same = _wrapped("om_1", "oc_team", "ou_user", "x")
    applied = []
    stats = process_lines(
        _ndjson(same, same, same),
        team_agents=_AGENTS,
        chat_id="oc_team",
        apply_fn=applied.append,
    )
    assert stats.handled == 1
    assert stats.dropped == 2
    assert stats.drops_by_reason.get("dedup") == 2


def test_invalid_json_is_dropped_with_bad_json_reason():
    stats = process_lines(
        ["not-json", json.dumps(_wrapped("om_1", "oc_team", "ou", "hi"))],
        team_agents=_AGENTS,
        chat_id="oc_team",
        apply_fn=lambda d: None,
    )
    assert stats.dropped == 1
    assert stats.handled == 1
    assert stats.drops_by_reason.get("bad_json") == 1


def test_blank_lines_are_skipped_silently():
    stats = process_lines(
        ["", "  ", "\n", json.dumps(_wrapped("om_1", "oc_team", "ou", "hi"))],
        team_agents=_AGENTS,
        chat_id="oc_team",
        apply_fn=lambda d: None,
    )
    assert stats.handled == 1
    assert stats.dropped == 0


def test_cross_team_chat_id_is_dropped():
    stats = process_lines(
        _ndjson(_wrapped("om_1", "oc_other", "ou", "hi")),
        team_agents=_AGENTS,
        chat_id="oc_team",
        apply_fn=lambda d: None,
    )
    assert stats.dropped == 1
    assert "cross_team" in stats.drops_by_reason


def test_bot_self_messages_are_dropped():
    stats = process_lines(
        _ndjson(_wrapped("om_1", "oc_team", "ou_bot", "hi")),
        team_agents=_AGENTS,
        chat_id="oc_team",
        bot_id="ou_bot",
        apply_fn=lambda d: None,
    )
    assert stats.dropped == 1
    assert "bot_self" in stats.drops_by_reason


def test_mention_routes_to_specific_worker():
    applied = []
    stats = process_lines(
        _ndjson(_wrapped("om_1", "oc_team", "ou_user", "@worker_codex review")),
        team_agents=_AGENTS,
        chat_id="oc_team",
        apply_fn=applied.append,
    )
    assert stats.handled == 1
    assert applied[0].targets == ["worker_codex"]


def test_progress_callback_invoked_per_handled_event():
    applied = []
    progress = []

    def on_progress(decision, stats):
        progress.append((decision.msg_id, stats.handled, stats.dropped))

    process_lines(
        _ndjson(
            _wrapped("om_1", "oc_team", "ou_user", "hi"),
            _wrapped("om_2", "oc_team", "ou_user", "@worker_cc"),
        ),
        team_agents=_AGENTS,
        chat_id="oc_team",
        apply_fn=applied.append,
        on_progress=on_progress,
    )
    assert len(progress) == 2
    assert progress[0][0] == "om_1"
    assert progress[1][0] == "om_2"


def test_seen_msg_ids_grows_only_with_handled_events():
    stats = process_lines(
        _ndjson(
            _wrapped("om_1", "oc_team", "ou_user", "hi"),
            _wrapped("om_2", "oc_other", "ou_user", "cross-team drop"),
        ),
        team_agents=_AGENTS,
        chat_id="oc_team",
        apply_fn=lambda d: None,
    )
    # om_1 handled (added); om_2 cross_team dropped (not added)
    assert stats.seen_msg_ids == {"om_1"}


def test_normalises_flat_event_with_top_level_fields():
    """Some upstream variants emit flat events without the .event wrapper."""
    flat = json.dumps({
        "message_id": "om_x",
        "chat_id": "oc_team",
        "sender_id": "ou_user",
        "text": "hi",
        "msg_type": "text",
    })
    applied = []
    stats = process_lines(
        [flat],
        team_agents=_AGENTS,
        chat_id="oc_team",
        apply_fn=applied.append,
    )
    assert stats.handled == 1
    assert applied[0].text == "hi"


def test_default_target_param_routes_human_messages_elsewhere():
    applied = []
    process_lines(
        _ndjson(_wrapped("om_1", "oc_team", "ou_user", "anything")),
        team_agents=_AGENTS,
        chat_id="oc_team",
        default_target="worker_cc",
        apply_fn=applied.append,
    )
    assert applied[0].targets == ["worker_cc"]
