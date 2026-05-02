"""Tests for feishu/catchup.py — cursor + replay-on-restart."""
from __future__ import annotations

import json

from helpers import isolated_env
from claudeteam.feishu import catchup
from claudeteam.feishu.router import Action, Decision
from claudeteam.runtime import paths


# ── cursor I/O ──────────────────────────────────────────────────


def test_read_cursor_empty_when_file_missing():
    with isolated_env():
        assert catchup.read_cursor() == {}


def test_write_then_read_roundtrip():
    with isolated_env():
        catchup.write_cursor("om_42", "1719000000000")
        cur = catchup.read_cursor()
        assert cur["message_id"] == "om_42"
        assert cur["create_time"] == "1719000000000"


def test_write_cursor_skips_when_either_field_blank():
    with isolated_env():
        catchup.write_cursor("", "1234")
        catchup.write_cursor("om_x", "")
        assert not paths.router_cursor_file().exists()


def test_read_cursor_returns_empty_on_garbage_json():
    with isolated_env():
        paths.ensure_state_dir()
        paths.router_cursor_file().write_text("not json", encoding="utf-8")
        assert catchup.read_cursor() == {}


def test_record_decision_advances_cursor_for_route():
    decision = Decision(action=Action.ROUTE, targets=["m"], text="x",
                        msg_id="om_99", create_time="1719999999000")
    with isolated_env():
        catchup.record_decision(decision)
        cur = catchup.read_cursor()
        assert cur["message_id"] == "om_99"


def test_record_decision_advances_cursor_for_drop_with_msg_id():
    decision = Decision(action=Action.DROP, msg_id="om_drop",
                        create_time="1720000000000", reason="empty")
    with isolated_env():
        catchup.record_decision(decision)
        cur = catchup.read_cursor()
        assert cur["message_id"] == "om_drop"


def test_record_decision_skips_when_no_msg_id_or_create_time():
    decision = Decision(action=Action.DROP, reason="no_msg_id")
    with isolated_env():
        catchup.record_decision(decision)
        assert not paths.router_cursor_file().exists()


# ── pending_lines ───────────────────────────────────────────────


def _msg(msg_id, create_time, *, text="hi", chat_id="oc_x", sender="ou_user"):
    return {
        "message_id": msg_id,
        "create_time": create_time,
        "chat_id": chat_id,
        "msg_type": "text",
        "sender": {"id": sender, "id_type": "open_id"},
        "body": {"content": json.dumps({"text": text})},
    }


def test_pending_lines_returns_only_messages_newer_than_cursor():
    history = [
        _msg("om_old1", "1000"),
        _msg("om_old2", "1500"),
        _msg("om_new1", "2500"),
        _msg("om_new2", "3000"),
    ]
    with isolated_env():
        catchup.write_cursor("om_old2", "1500")
        lines = catchup.pending_lines("oc_x", list_fn=lambda: history)
    assert len(lines) == 2
    parsed = [json.loads(line) for line in lines]
    ids = [p["event"]["message"]["message_id"] for p in parsed]
    assert ids == ["om_new1", "om_new2"]


def test_pending_lines_returns_all_when_no_cursor():
    history = [_msg("om_a", "100"), _msg("om_b", "200")]
    with isolated_env():
        lines = catchup.pending_lines("oc_x", list_fn=lambda: history)
    assert len(lines) == 2


def test_pending_lines_sorts_oldest_first_even_when_history_newest_first():
    history = [_msg("om_c", "300"), _msg("om_a", "100"), _msg("om_b", "200")]
    with isolated_env():
        lines = catchup.pending_lines("oc_x", list_fn=lambda: history)
    parsed = [json.loads(l) for l in lines]
    cts = [p["event"]["message"]["create_time"] for p in parsed]
    assert cts == ["100", "200", "300"]


def test_pending_lines_emits_subscribe_compatible_shape():
    history = [_msg("om_x", "500", text="hello world", sender="ou_42")]
    with isolated_env():
        lines = catchup.pending_lines("oc_chat", list_fn=lambda: history)
    line = json.loads(lines[0])
    msg = line["event"]["message"]
    assert msg["message_id"] == "om_x"
    assert msg["chat_id"] == "oc_x"
    assert msg["create_time"] == "500"
    assert json.loads(msg["content"])["text"] == "hello world"
    assert line["event"]["sender"]["sender_id"]["open_id"] == "ou_42"


def test_pending_lines_returns_empty_when_history_empty():
    with isolated_env():
        catchup.write_cursor("om_anchor", "1000")
        lines = catchup.pending_lines("oc_x", list_fn=lambda: [])
    assert lines == []


def test_pending_lines_skips_messages_with_bad_create_time():
    history = [
        _msg("om_ok", "1000"),
        {"message_id": "om_bad", "create_time": "not-a-number",
         "chat_id": "oc_x", "msg_type": "text",
         "sender": {"id": "ou_x"}, "body": {"content": "{}"}},
    ]
    with isolated_env():
        lines = catchup.pending_lines("oc_x", list_fn=lambda: history)
    parsed = [json.loads(l) for l in lines]
    ids = [p["event"]["message"]["message_id"] for p in parsed]
    assert ids == ["om_ok"]


# ── round-trip via subscribe.process_lines ──────────────────────


def test_pending_lines_round_trip_through_process_lines():
    """Ensure the lines we emit can be eaten by subscribe.process_lines."""
    from claudeteam.feishu.subscribe import process_lines

    history = [_msg("om_replay", "5000", text="catch this")]
    with isolated_env():
        lines = catchup.pending_lines("oc_x", list_fn=lambda: history)
        applies = []
        stats = process_lines(
            lines,
            team_agents=["manager"],
            chat_id="oc_x",
            apply_fn=lambda d: applies.append(d),
        )
    assert stats.handled == 1
    assert applies[0].text == "catch this"
    assert applies[0].create_time == "5000"
