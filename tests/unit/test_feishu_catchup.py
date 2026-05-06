"""Tests for feishu/catchup.py — cursor + replay-on-restart."""
from __future__ import annotations

import json

from helpers import isolated_env
from claudeteam.feishu import catchup
from claudeteam.feishu.router import Action, Decision
from claudeteam.feishu.subscribe import process_lines
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


def test_pending_lines_default_list_fn_uses_bot_when_env_says_bot():
    """Bot-only deploys (no `lark-cli auth login` user) trip
    `need_user_authorization` from chat.list_recent's historical
    as_user=True default. Honor CLAUDETEAM_LARK_SEND_AS=bot like
    `say` does so the router catchup actually fetches."""
    captured = {}
    from claudeteam.feishu import chat as _chat
    real_list_recent = _chat.list_recent
    def spy(chat_id, **kw):
        captured["as_user"] = kw.get("as_user")
        return []
    _chat.list_recent = spy
    try:
        from helpers import env_patch
        with isolated_env(), env_patch(CLAUDETEAM_LARK_SEND_AS="bot"):
            catchup.pending_lines("oc_x")
        assert captured["as_user"] is False
    finally:
        _chat.list_recent = real_list_recent


def test_pending_lines_default_list_fn_keeps_user_default_when_env_unset():
    captured = {}
    from claudeteam.feishu import chat as _chat
    real_list_recent = _chat.list_recent
    def spy(chat_id, **kw):
        captured["as_user"] = kw.get("as_user")
        return []
    _chat.list_recent = spy
    try:
        from helpers import env_patch
        with isolated_env(), env_patch(CLAUDETEAM_LARK_SEND_AS=None):
            catchup.pending_lines("oc_x")
        assert captured["as_user"] is True
    finally:
        _chat.list_recent = real_list_recent


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


# ── live lark-cli 1.0.21 response shape (REGRESSION ROUND-56) ───
#
# `lark-cli im +chat-messages-list` emits messages with content at TOP
# LEVEL (not under .body.content) AND create_time as a human-readable
# string ("2026-05-03 18:53"), not epoch ms. Prior to round-56,
# catchup silently dropped every replayed message because:
#   - body.get("content") returned "" (no .body key in live shape)
#   - int("2026-05-03 ...") raised ValueError → message skipped
# Tests below pin the live shape so we don't regress.


def _msg_live(msg_id, create_time_iso, *, text="hi", chat_id="oc_x", sender="ou_user"):
    """Mirror lark-cli 1.0.21+ chat-messages-list shape: content at top
    level, create_time as 'YYYY-MM-DD HH:MM[:SS]'."""
    return {
        "message_id": msg_id,
        "create_time": create_time_iso,
        "chat_id": chat_id,
        "msg_type": "text",
        "sender": {"id": sender, "id_type": "open_id"},
        "content": json.dumps({"text": text}),
    }


def test_pending_lines_handles_live_lark_cli_shape():
    """REGRESSION: live shape has content at top + ISO create_time. Old
    catchup silently dropped these as 'empty' / unparseable."""
    history = [
        _msg_live("om_live_1", "2026-05-03 18:50",
                  text="hello from live shape"),
    ]
    with isolated_env():
        catchup.write_cursor("om_old", "1700000000000")  # in 2023
        lines = catchup.pending_lines("oc_x", list_fn=lambda: history)
    assert len(lines) == 1, (
        f"live-shape message should be kept; got {len(lines)} lines")
    payload = json.loads(lines[0])
    msg = payload["event"]["message"]
    assert msg["message_id"] == "om_live_1"
    # Content survives the conversion
    assert "hello from live shape" in msg["content"]


def test_pending_lines_iso_time_compared_correctly_against_epoch_cursor():
    """The cursor stores epoch ms (set by record_decision from subscribe
    events), but list_recent returns ISO strings. The comparator must
    coerce both to the same scale."""
    # 2026-05-03 18:50 local ≈ 1777805400000 ish
    history = [
        _msg_live("om_before", "2026-05-03 17:00"),
        _msg_live("om_after", "2026-05-03 19:00"),
    ]
    with isolated_env():
        # cursor in epoch ms, between the two ISO times above
        # 2026-05-03 18:00 local = ~1777801200000
        catchup.write_cursor("om_cursor", "1777801200000")
        lines = catchup.pending_lines("oc_x", list_fn=lambda: history)
    parsed = [json.loads(l) for l in lines]
    ids = [p["event"]["message"]["message_id"] for p in parsed]
    # only om_after should survive (newer than cursor's epoch)
    assert ids == ["om_after"], f"expected only om_after, got {ids}"


def test_pending_lines_round_trip_with_live_shape_through_process_lines():
    """End-to-end: replay using the live shape, the events go through
    subscribe.process_lines and produce a real handled Decision (not
    silently dropped as 'empty')."""
    history = [_msg_live("om_replay_live", "2026-05-03 19:00",
                          text="catch this live")]
    with isolated_env():
        lines = catchup.pending_lines("oc_x", list_fn=lambda: history)
        applies = []
        stats = process_lines(
            lines,
            team_agents=["manager"],
            chat_id="oc_x",
            apply_fn=lambda d: applies.append(d),
        )
    assert stats.handled == 1, (
        f"live-shape replay should produce 1 handled, got {dict(stats.drops_by_reason)}")
    assert applies[0].text == "catch this live"
