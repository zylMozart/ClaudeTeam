#!/usr/bin/env python3
"""No-live tests for Feishu IM send command construction."""
from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

from claudeteam.integrations.feishu.client import _lark_im_send_with_run


def capture_call(**kwargs):
    calls = []
    result = _lark_im_send_with_run(lambda args: calls.append(args) or {"ok": True}, "chat", **kwargs)
    assert result == {"ok": True}
    assert calls
    return calls[0]


def test_send_without_reply_uses_messages_send():
    args = capture_call(content="hello")
    assert args[:4] == ["im", "+messages-send", "--chat-id", "chat"]
    assert "--text" in args
    assert "hello" in args


def test_reply_uses_messages_reply():
    args = capture_call(content="hello", reply_to="om_1")
    assert args[:4] == ["im", "+messages-reply", "--message-id", "om_1"]
    assert "--chat-id" not in args
    assert "--text" in args


def test_reply_in_thread_adds_flag():
    args = capture_call(content="hello", reply_to="om_1", reply_in_thread=True)
    assert "--reply-in-thread" in args


def test_post_content_uses_msg_type_post():
    args = capture_call(content='{"zh_cn":{}}', msg_type="post")
    assert "--content" in args
    assert "--msg-type" in args
    assert args[args.index("--msg-type") + 1] == "post"


def main() -> int:
    tests = [v for k, v in sorted(globals().items()) if k.startswith("test_")]
    passed = failed = 0
    for fn in tests:
        try:
            fn()
            print(f"  ok {fn.__name__}")
            passed += 1
        except Exception as exc:
            print(f"  fail {fn.__name__}: {exc}")
            failed += 1
    print(f"\nfeishu client send tests: {passed}/{passed + failed} passed")
    return 0 if failed == 0 else 1


if __name__ == "__main__":
    raise SystemExit(main())
