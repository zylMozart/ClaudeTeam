"""Tests for feishu/chat.py — chat operations using a fake lark_run."""
from __future__ import annotations

from helpers import CallRecorder as _Spy
from claudeteam.feishu import chat


def test_send_text_passes_chat_id_text_and_bot_identity_by_default():
    spy = _Spy({"message_id": "om_1"})
    out = chat.send_text("oc_xxx", "hello", lark_run=spy)
    assert out == {"message_id": "om_1"}
    args = spy.calls[0]["args"]
    assert "im" in args and "+messages-send" in args
    assert "--chat-id" in args and "oc_xxx" in args
    assert "--text" in args and "hello" in args
    assert "--as" in args and "bot" in args


def test_send_text_as_user_when_flag_set():
    spy = _Spy({})
    chat.send_text("oc_x", "x", as_user=True, lark_run=spy)
    args = spy.calls[0]["args"]
    i = args.index("--as")
    assert args[i + 1] == "user"


def test_send_text_routes_to_messages_reply_when_reply_to_set():
    """REGRESSION (round 7 D1): lark-cli +messages-send has no --reply-to;
    replies must go through +messages-reply with --message-id."""
    spy = _Spy({})
    chat.send_text("oc_x", "x", reply_to="om_parent", lark_run=spy)
    args = spy.calls[0]["args"]
    assert "+messages-reply" in args
    assert "+messages-send" not in args
    assert "--message-id" in args
    assert "om_parent" in args
    # No --reply-to flag (lark-cli rejects it)
    assert "--reply-to" not in args


def test_send_text_uses_messages_send_when_no_reply_to():
    spy = _Spy({})
    chat.send_text("oc_x", "x", lark_run=spy)
    args = spy.calls[0]["args"]
    assert "+messages-send" in args
    assert "+messages-reply" not in args
    assert "--chat-id" in args and "oc_x" in args


def test_send_text_returns_none_when_chat_id_empty():
    spy = _Spy({})
    assert chat.send_text("", "x", lark_run=spy) is None
    assert spy.calls == []  # never even called lark


def test_send_text_threads_profile_through_to_lark_run():
    spy = _Spy({})
    chat.send_text("oc_x", "x", profile="prod", lark_run=spy)
    assert spy.calls[0]["kwargs"]["profile"] == "prod"


def test_send_card_uses_msg_type_interactive_with_json_content():
    spy = _Spy({})
    chat.send_card("oc_x", {"title": "hi"}, lark_run=spy)
    args = spy.calls[0]["args"]
    assert "--msg-type" in args and "interactive" in args
    assert "--content" in args
    # content is a JSON-encoded string
    content = args[args.index("--content") + 1]
    assert content.startswith("{") and "title" in content


def test_list_recent_returns_messages_list():
    spy = _Spy({"messages": [{"id": 1}, {"id": 2}], "has_more": False})
    out = chat.list_recent("oc_x", lark_run=spy)
    assert out == [{"id": 1}, {"id": 2}]


def test_list_recent_returns_empty_when_chat_id_blank():
    spy = _Spy({})
    assert chat.list_recent("", lark_run=spy) == []
    assert spy.calls == []


def test_list_recent_returns_empty_when_lark_returns_none():
    spy = _Spy(None)
    assert chat.list_recent("oc_x", lark_run=spy) == []


def test_list_recent_uses_user_identity_by_default():
    spy = _Spy({"messages": []})
    chat.list_recent("oc_x", lark_run=spy)
    args = spy.calls[0]["args"]
    assert args[args.index("--as") + 1] == "user"


def test_list_recent_can_override_to_bot_identity():
    """When the user OAuth profile has expired or isn't available,
    callers can fall back to bot — provided the app has chat-history
    read scope. Verify the override is wired correctly."""
    spy = _Spy({"messages": []})
    chat.list_recent("oc_x", as_user=False, lark_run=spy)
    args = spy.calls[0]["args"]
    assert args[args.index("--as") + 1] == "bot"


def test_list_recent_threads_page_size_into_argv():
    spy = _Spy({"messages": []})
    chat.list_recent("oc_x", page_size=50, lark_run=spy)
    args = spy.calls[0]["args"]
    assert "--page-size" in args
    assert args[args.index("--page-size") + 1] == "50"


def test_list_recent_threads_profile_through_to_lark_run():
    spy = _Spy({"messages": []})
    chat.list_recent("oc_x", profile="prod", lark_run=spy)
    assert spy.calls[0]["kwargs"]["profile"] == "prod"


def test_list_recent_handles_missing_messages_field():
    """If lark-cli returns a `data` dict that has no `messages` key
    (e.g. the chat is genuinely empty), list_recent should return []
    not crash on the .get(...) chain."""
    spy = _Spy({"has_more": False})  # no "messages" key
    assert chat.list_recent("oc_x", lark_run=spy) == []


def test_send_card_returns_none_when_chat_id_empty():
    """Sister to send_text's same guard — silently skip on empty
    chat_id rather than letting lark-cli error."""
    spy = _Spy({})
    assert chat.send_card("", {"title": "x"}, lark_run=spy) is None
    assert spy.calls == []


def test_send_card_threads_profile_and_identity_through():
    spy = _Spy({"message_id": "om_card"})
    out = chat.send_card("oc_x", {"title": "hi"}, profile="prod",
                         as_user=True, lark_run=spy)
    assert out == {"message_id": "om_card"}
    assert spy.calls[0]["kwargs"]["profile"] == "prod"
    args = spy.calls[0]["args"]
    assert args[args.index("--as") + 1] == "user"
