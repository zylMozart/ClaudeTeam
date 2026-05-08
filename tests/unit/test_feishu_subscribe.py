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


def test_human_message_routes_to_manager_r174():
    """R174: human chat messages always route to manager — even
    those with `@worker_X` text. Manager parses intent and dispatches
    via `claudeteam send`. Verifies the subscribe→classify→apply
    chain emits a Decision targeting only manager."""
    applied = []
    stats = process_lines(
        _ndjson(_wrapped("om_1", "oc_team", "ou_user", "@worker_codex review")),
        team_agents=_AGENTS,
        chat_id="oc_team",
        apply_fn=applied.append,
    )
    assert stats.handled == 1
    assert applied[0].targets == ["manager"]


def test_progress_callback_failure_does_not_kill_loop():
    """REGRESSION: in production, on_progress is catchup.record_decision
    which writes to disk via atomic_write_text. Disk full / permission
    denied / tmp-replace race could raise — that must NOT kill the
    daemon. Cursor staleness recovers on next event; daemon death does
    not. Verifies the try/except inside process_lines."""
    applied = []
    progress_calls = []

    def flaky_on_progress(decision, stats):
        progress_calls.append(decision.msg_id)
        # First call succeeds, second raises, third succeeds
        if len(progress_calls) == 2:
            raise OSError("disk full")

    stats = process_lines(
        _ndjson(
            _wrapped("om_a", "oc_team", "ou_user", "first"),
            _wrapped("om_b", "oc_team", "ou_user", "second"),  # cursor write raises
            _wrapped("om_c", "oc_team", "ou_user", "third"),
        ),
        team_agents=_AGENTS,
        chat_id="oc_team",
        apply_fn=applied.append,
        on_progress=flaky_on_progress,
    )
    # All three events were handled — second one's cursor failure
    # didn't propagate. Loop kept going.
    assert stats.handled == 3
    assert len(progress_calls) == 3
    assert len(applied) == 3


def test_apply_fn_failure_does_not_kill_loop_and_does_not_dedup_msg():
    """REGRESSION (round-63): an unhandled exception out of apply_fn
    used to kill the router daemon AND (worse) the seen-add happened
    BEFORE apply, so a retry path (catchup, stream re-receive) would
    silently dedup the failed-to-apply message. Now: apply_fn errors
    are caught + counted as 'apply_error' drops; msg_id is NOT marked
    seen so retry can re-process."""
    apply_calls = []

    def flaky_apply(decision):
        apply_calls.append(decision.msg_id)
        # Second event raises; first and third succeed
        if len(apply_calls) == 2:
            raise RuntimeError("transient adapter resolution failure")

    stats = process_lines(
        _ndjson(
            _wrapped("om_a", "oc_team", "ou_user", "first"),
            _wrapped("om_b", "oc_team", "ou_user", "second"),  # apply raises
            _wrapped("om_c", "oc_team", "ou_user", "third"),
        ),
        team_agents=_AGENTS,
        chat_id="oc_team",
        apply_fn=flaky_apply,
    )
    # All 3 apply_fn invocations attempted
    assert len(apply_calls) == 3
    # Only the two that succeeded count as handled
    assert stats.handled == 2
    # The failure is recorded as a drop with reason "apply_error"
    assert stats.dropped == 1
    assert stats.drops_by_reason.get("apply_error") == 1
    # Critical: failed msg_id NOT in seen → retry could re-process
    assert "om_b" not in stats.seen_msg_ids
    # Successful ones ARE in seen
    assert "om_a" in stats.seen_msg_ids
    assert "om_c" in stats.seen_msg_ids


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


def test_normalises_real_lark_cli_compact_wire_format():
    """REGRESSION: round 3 smoke captured this exact shape from
    \`npx @larksuite/cli event +subscribe --compact\` (lark-cli 1.0.21).
    Top-level fields, content as a plain string (NOT JSON-encoded), and
    message_type rather than msg_type. The pre-fix _normalise dropped
    these as text="" → reason="empty"."""
    real = json.dumps({
        "chat_id": "oc_989e33567a4be168c7e7a286287a3965",
        "chat_type": "group",
        "content": "[boss] @worker_codex hello round-trip",
        "create_time": "1777758788527",
        "id": "om_x100b50536a8a94a0c457f151f14c25b",
        "message_id": "om_x100b50536a8a94a0c457f151f14c25b",
        "message_type": "text",
        "sender_id": "ou_72716731212dbea7a5614cf21719bc75",
        "timestamp": "1777758788697",
        "type": "im.message.receive_v1",
    })
    applied = []
    stats = process_lines(
        [real],
        team_agents=_AGENTS,
        chat_id="oc_989e33567a4be168c7e7a286287a3965",
        apply_fn=applied.append,
    )
    assert stats.handled == 1, f"expected 1 handled, got drops {dict(stats.drops_by_reason)}"
    assert applied[0].text == "[boss] @worker_codex hello round-trip"
    # R174: human messages → manager regardless of @-mention
    assert applied[0].targets == ["manager"]
    assert applied[0].msg_id == "om_x100b50536a8a94a0c457f151f14c25b"


def test_normalises_real_lark_cli_compact_with_json_encoded_content():
    """Same wire format but content is JSON-encoded {"text": "..."} —
    the older Feishu-webhook style some lark-cli versions still emit."""
    real = json.dumps({
        "chat_id": "oc_team",
        "content": '{"text": "@worker_codex hi"}',
        "message_id": "om_1",
        "message_type": "text",
        "sender_id": "ou_user",
        "type": "im.message.receive_v1",
    })
    applied = []
    stats = process_lines(
        [real],
        team_agents=_AGENTS,
        chat_id="oc_team",
        apply_fn=applied.append,
    )
    assert stats.handled == 1
    assert applied[0].text == "@worker_codex hi"


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


# ── B.1 image / file / audio messages ─────────────────────────────


def test_normalises_image_message_to_placeholder_text():
    """REGRESSION (Round B.1): image messages used to drop as 'empty'
    because content didn't include a 'text' field. Now produces a
    placeholder so the router can route the message and the worker
    knows something arrived."""
    line = json.dumps({
        "message_id": "om_img1",
        "chat_id": "oc_team",
        "sender_id": "ou_user",
        "message_type": "image",
        "content": json.dumps({"image_key": "img_v3_xxx"}),
    })
    applied = []
    stats = process_lines([line], team_agents=_AGENTS,
                          chat_id="oc_team", apply_fn=applied.append)
    assert stats.handled == 1
    assert "image_key=img_v3_xxx" in applied[0].text
    assert "[image:" in applied[0].text


def test_normalises_image_message_no_key_falls_back_to_bracket():
    line = json.dumps({
        "message_id": "om_img2",
        "chat_id": "oc_team",
        "sender_id": "ou_user",
        "message_type": "image",
        "content": "{}",
    })
    applied = []
    stats = process_lines([line], team_agents=_AGENTS,
                          chat_id="oc_team", apply_fn=applied.append)
    assert stats.handled == 1
    assert applied[0].text == "[image]"


def test_normalises_file_message_with_filename():
    line = json.dumps({
        "message_id": "om_file1",
        "chat_id": "oc_team",
        "sender_id": "ou_user",
        "message_type": "file",
        "content": json.dumps({
            "file_name": "report.pdf",
            "file_key": "file_v2_xxx",
        }),
    })
    applied = []
    stats = process_lines([line], team_agents=_AGENTS,
                          chat_id="oc_team", apply_fn=applied.append)
    assert stats.handled == 1
    assert "report.pdf" in applied[0].text
    assert "file_key=file_v2_xxx" in applied[0].text


def test_normalises_file_message_filename_only():
    line = json.dumps({
        "message_id": "om_file2",
        "chat_id": "oc_team",
        "sender_id": "ou_user",
        "message_type": "file",
        "content": json.dumps({"file_name": "notes.txt"}),
    })
    applied = []
    stats = process_lines([line], team_agents=_AGENTS,
                          chat_id="oc_team", apply_fn=applied.append)
    assert stats.handled == 1
    assert applied[0].text == "[file: notes.txt]"


def test_normalises_audio_message():
    line = json.dumps({
        "message_id": "om_audio1",
        "chat_id": "oc_team",
        "sender_id": "ou_user",
        "message_type": "audio",
        "content": json.dumps({"file_key": "audio_xxx"}),
    })
    applied = []
    stats = process_lines([line], team_agents=_AGENTS,
                          chat_id="oc_team", apply_fn=applied.append)
    assert stats.handled == 1
    assert "[audio:" in applied[0].text
    assert "audio_xxx" in applied[0].text


def test_normalises_sticker_message():
    line = json.dumps({
        "message_id": "om_stk1",
        "chat_id": "oc_team",
        "sender_id": "ou_user",
        "message_type": "sticker",
        "content": json.dumps({"file_key": "stk_xxx"}),
    })
    applied = []
    stats = process_lines([line], team_agents=_AGENTS,
                          chat_id="oc_team", apply_fn=applied.append)
    assert stats.handled == 1
    assert "[sticker: stk_xxx]" in applied[0].text


def test_normalises_post_text_only_message():
    """Boss-flagged 2026-05-06: 飞书富文本 (post) 消息要被路由到 manager
    inbox, 不能丢. 纯文字段落场景."""
    line = json.dumps({
        "message_id": "om_post1",
        "chat_id": "oc_team",
        "sender_id": "ou_user",
        "message_type": "post",
        "content": json.dumps({
            "title": "标题",
            "content": [
                [{"tag": "text", "text": "第一段文字"}],
                [{"tag": "text", "text": "第二段文字"}],
            ],
        }),
    })
    applied = []
    stats = process_lines([line], team_agents=_AGENTS,
                          chat_id="oc_team", apply_fn=applied.append)
    assert stats.handled == 1
    text = applied[0].text
    assert "标题" in text
    assert "第一段文字" in text
    assert "第二段文字" in text


def test_normalises_post_text_plus_image_message():
    """图 + 文混合: 老板典型场景 (发个截图 + 说 "看这个 bug")."""
    line = json.dumps({
        "message_id": "om_post2",
        "chat_id": "oc_team",
        "sender_id": "ou_user",
        "message_type": "post",
        "content": json.dumps({
            "title": "",
            "content": [
                [
                    {"tag": "text", "text": "看这个 bug "},
                    {"tag": "img", "image_key": "img_screenshot"},
                ],
            ],
        }),
    })
    applied = []
    stats = process_lines([line], team_agents=_AGENTS,
                          chat_id="oc_team", apply_fn=applied.append)
    assert stats.handled == 1
    text = applied[0].text
    assert "看这个 bug" in text
    assert "[image: image_key=img_screenshot]" in text


def test_normalises_post_text_plus_file_message():
    """文件 + 文字混合."""
    line = json.dumps({
        "message_id": "om_post3",
        "chat_id": "oc_team",
        "sender_id": "ou_user",
        "message_type": "post",
        "content": json.dumps({
            "title": "",
            "content": [
                [
                    {"tag": "text", "text": "请评审这份: "},
                    {"tag": "file", "file_name": "spec.pdf",
                     "file_key": "file_v2_abc"},
                ],
            ],
        }),
    })
    applied = []
    stats = process_lines([line], team_agents=_AGENTS,
                          chat_id="oc_team", apply_fn=applied.append)
    assert stats.handled == 1
    text = applied[0].text
    assert "请评审这份" in text
    assert "spec.pdf" in text
    assert "file_v2_abc" in text


def test_normalises_post_with_link_and_at_mention():
    """post 里的超链接 + @人 也要可见."""
    line = json.dumps({
        "message_id": "om_post4",
        "chat_id": "oc_team",
        "sender_id": "ou_user",
        "message_type": "post",
        "content": json.dumps({
            "title": "",
            "content": [
                [
                    {"tag": "at", "user_id": "ou_xyz"},
                    {"tag": "text", "text": " 看 "},
                    {"tag": "a", "text": "这个文档", "href": "https://x.test/doc"},
                ],
            ],
        }),
    })
    applied = []
    stats = process_lines([line], team_agents=_AGENTS,
                          chat_id="oc_team", apply_fn=applied.append)
    assert stats.handled == 1
    text = applied[0].text
    assert "@ou_xyz" in text
    assert "这个文档" in text
    assert "https://x.test/doc" in text


def test_text_message_extraction_unchanged_after_b1():
    """Regression: text-message extraction still works after _extract_text
    refactor."""
    line = json.dumps({
        "message_id": "om_t",
        "chat_id": "oc_team",
        "sender_id": "ou_user",
        "message_type": "text",
        "content": json.dumps({"text": "hello world"}),
    })
    applied = []
    process_lines([line], team_agents=_AGENTS,
                  chat_id="oc_team", apply_fn=applied.append)
    assert applied[0].text == "hello world"


def test_on_line_received_fires_for_every_non_empty_line_including_drops():
    """Subscribe-aliveness ping fires per raw stdout line, BEFORE classify.
    Bot self-talk + dedup + bad-json all DROP, but subscribe is healthy
    (lark-cli still emits stdout) — so on_line_received must fire and
    bump the watchdog's stall timer. Without this, chats with mostly
    self-talk/dedup traffic trip the 600s stall threshold even though
    subscribe is alive (caught 2026-05-08 host smoke)."""
    fires = []
    bot_self_line = json.dumps({
        "event": {
            "message": {
                "message_id": "om_self",
                "chat_id": "oc_team",
                "message_type": "text",
                "content": json.dumps({"text": "echo"}),
            },
            "sender": {"sender_id": {"open_id": "ou_bot"}, "sender_type": "app"},
        }
    })
    bad = "not-valid-json"
    human_line = json.dumps(_wrapped("om_h", "oc_team", "ou_user", "hi"))
    stats = process_lines(
        iter([bot_self_line, bad, human_line]),
        team_agents=_AGENTS,
        chat_id="oc_team",
        bot_id="ou_bot",
        apply_fn=lambda d: None,
        on_line_received=lambda: fires.append(1),
    )
    # 3 non-empty lines → 3 fires regardless of drop/handled
    assert len(fires) == 3
    # Confirm 2 of them DID drop (bot_self + bad_json)
    assert stats.dropped >= 2


def test_on_line_received_callback_failure_does_not_kill_loop():
    """The aliveness callback runs first, before parse. A buggy callback
    must not kill subscribe — subscribe stalling is a worse outcome
    than missing one heartbeat. Verifies the try/except wrapper."""
    fires = []
    def flaky():
        fires.append(1)
        raise RuntimeError("buggy probe")
    stats = process_lines(
        iter([json.dumps(_wrapped("om_h", "oc_team", "ou_user", "hi"))]),
        team_agents=_AGENTS,
        chat_id="oc_team",
        apply_fn=lambda d: None,
        on_line_received=flaky,
    )
    assert fires == [1]  # callback ran
    assert stats.handled == 1  # loop kept going
