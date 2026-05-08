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


def test_pending_lines_returns_only_messages_at_or_after_cursor_minute():
    """`>=` minute-floor semantics. Cutoff is floored to the minute so
    REST minute-precision messages aligned with the cursor's minute are
    included. Documented in feishu/catchup._newer_than."""
    # Use real minute-aligned epoch ms so the floor doesn't collapse to 0
    minute_a = "1778047620000"   # 2026-05-06 14:07:00
    minute_b = "1778047680000"   # 2026-05-06 14:08:00
    minute_c = "1778047740000"   # 2026-05-06 14:09:00
    history = [
        _msg("om_a1", minute_a),                 # before cursor minute → drop
        _msg("om_b1", minute_b),                 # at cursor minute → keep
        _msg("om_b2", "1778047712000"),          # cursor itself, sub-minute → keep
        _msg("om_c1", minute_c),                 # after cursor → keep
    ]
    with isolated_env():
        catchup.write_cursor("om_b2", "1778047712000")  # 14:08:32
        lines = catchup.pending_lines("oc_x", list_fn=lambda: history)
    import json
    ids = sorted(json.loads(l)["event"]["message"]["message_id"] for l in lines)
    # om_a1 (14:07) drops; om_b1, om_b2 (both 14:08), om_c1 (14:09) keep
    assert ids == ["om_b1", "om_b2", "om_c1"]


def test_pending_lines_recovers_messages_when_cursor_has_subminute_precision():
    """REGRESSION: 2026-05-06 host_smoke caught the deeper bug — cursor
    written from live events has millisecond precision, REST API
    list_recent returns minute precision strings. A bare `>=` still
    loses same-minute messages because REST 14:08:00 < cursor 14:08:32.
    Cutoff must floor to minute boundary."""
    cursor_ms = "1778047712107"  # 2026-05-06 14:08:32.107
    rest_minute = "2026-05-06 14:08"  # REST API shape, parses to 14:08:00
    history = [
        _msg("om_processed_via_live", cursor_ms),
        _msg("om_missed_a", rest_minute),
        _msg("om_missed_b", rest_minute),
    ]
    with isolated_env():
        catchup.write_cursor("om_processed_via_live", cursor_ms)
        lines = catchup.pending_lines("oc_x", list_fn=lambda: history)
    parsed = [json.loads(l) for l in lines]
    ids = sorted(p["event"]["message"]["message_id"] for p in parsed)
    # Both REST-precision missed messages must be returned despite REST
    # parsing to 14:08:00 < cursor's 14:08:32.107
    assert "om_missed_a" in ids
    assert "om_missed_b" in ids


def test_pending_lines_recovers_messages_at_same_minute_as_cursor():
    """REGRESSION: 2026-05-06 host_smoke caught lark WebSocket missing
    4 of 9 slash commands all sharing the same minute as the cursor.
    Strict `>` cutoff lost them permanently. With `>=`, they come back
    via catchup. Same minute simulated here as same epoch-ms."""
    same_minute = "1778047200000"
    history = [
        _msg("om_processed", same_minute),   # cursor lands here after live event
        _msg("om_missed_a", same_minute),    # lark WebSocket missed; same minute
        _msg("om_missed_b", same_minute),    # ditto
    ]
    with isolated_env():
        catchup.write_cursor("om_processed", same_minute)
        lines = catchup.pending_lines("oc_x", list_fn=lambda: history)
    # All 3 returned (cursor itself + 2 missed) so the missed ones get
    # a second chance; in-process dedup will skip om_processed if it's
    # still in seen_msg_ids.
    assert len(lines) == 3
    parsed = [json.loads(line) for line in lines]
    ids = [p["event"]["message"]["message_id"] for p in parsed]
    assert sorted(ids) == ["om_missed_a", "om_missed_b", "om_processed"]


def test_pending_lines_returns_empty_when_no_cursor():
    """Fresh deploy: catchup must NOT replay arbitrary chat history.
    Otherwise `claudeteam up` re-fires every recent message including
    old dispatches from a previous team. Round 2 host smoke caught
    this 2026-05-07: a fresh up replayed a 30-min-old r1-mix dispatch
    and manager re-dispatched workers for a task the boss had cleared.
    Live subscribe picks up from "now" forward; first live event
    writes the cursor so subsequent restarts catch up only the gap."""
    history = [_msg("om_a", "100"), _msg("om_b", "200")]
    with isolated_env():
        lines = catchup.pending_lines("oc_x", list_fn=lambda: history)
    assert lines == []


def test_pending_lines_sorts_oldest_first_even_when_history_newest_first():
    history = [_msg("om_c", "300"), _msg("om_a", "100"), _msg("om_b", "200")]
    # Need a cursor so pending_lines doesn't take the fresh-deploy
    # short-circuit; cursor at create_time=50 keeps everything.
    with isolated_env():
        catchup.write_cursor("om_seed", "50")
        lines = catchup.pending_lines("oc_x", list_fn=lambda: history)
    parsed = [json.loads(l) for l in lines]
    cts = [p["event"]["message"]["create_time"] for p in parsed]
    assert cts == ["100", "200", "300"]


def test_pending_lines_emits_subscribe_compatible_shape():
    history = [_msg("om_x", "500", text="hello world", sender="ou_42")]
    with isolated_env():
        catchup.write_cursor("seed", "1")
        lines = catchup.pending_lines("oc_chat", list_fn=lambda: history)
    line = json.loads(lines[0])
    msg = line["event"]["message"]
    assert msg["message_id"] == "om_x"
    assert msg["chat_id"] == "oc_x"
    assert msg["create_time"] == "500"
    assert json.loads(msg["content"])["text"] == "hello world"
    assert line["event"]["sender"]["sender_id"]["open_id"] == "ou_42"


def test_pending_lines_carries_post_message_through_to_subscribe():
    """Catchup 拉历史时遇到 post (图+文混合) 消息也要转成 subscribe NDJSON
    形状, msg_type=post 被 subscribe._normalise → _extract_text 处理."""
    post_content = json.dumps({
        "title": "",
        "content": [[
            {"tag": "text", "text": "看这个 "},
            {"tag": "img", "image_key": "img_screenshot"},
        ]],
    })
    history = [{
        "message_id": "om_post",
        "create_time": "1000",
        "chat_id": "oc_x",
        "msg_type": "post",
        "sender": {"id": "ou_boss", "id_type": "user"},
        "body": {"content": post_content},
    }]
    with isolated_env():
        catchup.write_cursor("seed", "1")
        lines = catchup.pending_lines("oc_chat", list_fn=lambda: history)
    line = json.loads(lines[0])
    msg = line["event"]["message"]
    assert msg["message_type"] == "post"
    # subscribe._normalise + _extract_text 已经在 subscribe 单测里覆盖
    # 把 post content → "[image: image_key=...]" + 文字, 这里 catchup
    # 只要保证 content 透传即可
    assert "img_screenshot" in msg["content"]


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
            catchup.write_cursor("seed", "1")
            catchup.pending_lines("oc_x")
        assert captured["as_user"] is False
    finally:
        _chat.list_recent = real_list_recent


def test_pending_lines_default_list_fn_honors_toml_send_as_bot():
    """Container deploy without env CLAUDETEAM_LARK_SEND_AS but with
    [feishu] send_as = "bot" in claudeteam.toml: catchup must respect
    the tunable and use bot identity. Boss-flagged 2026-05-06 host_smoke:
    bot-only container catchup got rc=2 because env var wasn't pinned
    in docker-compose; tunables fallback should cover that."""
    captured = {}
    from claudeteam.feishu import chat as _chat
    real_list_recent = _chat.list_recent
    def spy(chat_id, **kw):
        captured["as_user"] = kw.get("as_user")
        return []
    _chat.list_recent = spy
    try:
        from helpers import env_patch
        with isolated_env() as tmp:
            (tmp / "claudeteam.toml").write_text(
                '[feishu]\nsend_as = "bot"\n', encoding="utf-8")
            from claudeteam.runtime import tunables
            tunables.reset_cache()
            with env_patch(CLAUDETEAM_LARK_SEND_AS=None,
                            CLAUDETEAM_CONFIG_FILE=str(tmp / "claudeteam.toml")):
                catchup.write_cursor("seed", "1")
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
            catchup.write_cursor("seed", "1")
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
        catchup.write_cursor("seed", "1")
        lines = catchup.pending_lines("oc_x", list_fn=lambda: history)
    parsed = [json.loads(l) for l in lines]
    ids = [p["event"]["message"]["message_id"] for p in parsed]
    assert ids == ["om_ok"]


# ── round-trip via subscribe.process_lines ──────────────────────


def test_pending_lines_round_trip_through_process_lines():
    """Ensure the lines we emit can be eaten by subscribe.process_lines."""
    history = [_msg("om_replay", "5000", text="catch this")]
    with isolated_env():
        catchup.write_cursor("seed", "1")
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
        catchup.write_cursor("seed", "1")
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
