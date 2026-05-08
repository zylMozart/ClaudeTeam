#!/usr/bin/env python3
"""No-live tests for feishu_msg say reply and rich post support."""
from __future__ import annotations

import json
import os
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SCRIPTS = ROOT / "scripts"
if str(SCRIPTS) not in sys.path:
    sys.path.insert(0, str(SCRIPTS))

import feishu_msg
from claudeteam.commands.feishu_msg import parse_argv


class Patch:
    def __init__(self, obj, **items):
        self.obj = obj
        self.items = items
        self.old = {}

    def __enter__(self):
        for key, value in self.items.items():
            self.old[key] = getattr(self.obj, key)
            setattr(self.obj, key, value)

    def __exit__(self, exc_type, exc, tb):
        for key, value in self.old.items():
            setattr(self.obj, key, value)


def test_extract_image_key_shapes():
    assert feishu_msg._extract_image_key({"image_key": "img_1"}) == "img_1"
    assert feishu_msg._extract_image_key({"data": {"image_key": "img_2"}}) == "img_2"
    assert feishu_msg._extract_image_key({"image": {"image_key": "img_3"}}) == "img_3"
    assert feishu_msg._extract_image_key({"items": [{"image_key": "img_4"}]}) == "img_4"


def test_text_reply_passes_reply_to_first_card_only():
    calls = []
    with Patch(feishu_msg, CHAT=lambda: "chat"):
        with Patch(feishu_msg, ws_log=lambda *a, **k: None):
            with Patch(feishu_msg, _lark_im_send=lambda *a, **k: calls.append((a, k)) or {}):
                feishu_msg.cmd_say("manager", "hello", reply_to="om_1")
    assert calls
    assert calls[0][1]["reply_to"] == "om_1"
    assert calls[0][1]["reply_in_thread"] is False
    assert calls[0][1]["card"]


def test_image_reply_passes_image_and_reply_to():
    calls = []
    with tempfile.NamedTemporaryFile() as tmp:
        with Patch(feishu_msg, CHAT=lambda: "chat"):
            with Patch(feishu_msg, ws_log=lambda *a, **k: None):
                with Patch(feishu_msg, _lark_im_send=lambda *a, **k: calls.append((a, k)) or {}):
                    feishu_msg.cmd_say("manager", "", tmp.name, reply_to="om_1", reply_in_thread=True)
    assert calls
    assert calls[0][1]["image"] == tmp.name
    assert calls[0][1]["reply_to"] == "om_1"
    assert calls[0][1]["reply_in_thread"] is True


def test_text_and_image_sends_single_post():
    calls = []
    with tempfile.NamedTemporaryFile() as tmp:
        with Patch(feishu_msg, CHAT=lambda: "chat"):
            with Patch(feishu_msg, ws_log=lambda *a, **k: None):
                with Patch(feishu_msg, _lark_upload_image=lambda path: "img_key"):
                    with Patch(feishu_msg, _lark_im_send=lambda *a, **k: calls.append((a, k)) or {}):
                        feishu_msg.cmd_say("manager", "hello", tmp.name, reply_to="om_1")
    assert len(calls) == 1
    kwargs = calls[0][1]
    assert kwargs["msg_type"] == "post"
    assert kwargs["reply_to"] == "om_1"
    content = json.loads(kwargs["content"])
    rendered = json.dumps(content, ensure_ascii=False)
    assert "hello" in rendered
    assert "img_key" in rendered


def test_build_post_content_shape():
    post = feishu_msg.build_post_content("hello", "img_key", title="title")
    assert post["zh_cn"]["title"] == "title"
    assert post["zh_cn"]["content"][0][0] == {"tag": "text", "text": "hello"}
    assert post["zh_cn"]["content"][1][0] == {"tag": "img", "image_key": "img_key"}


def test_pure_parser_accepts_reply_flags():
    parsed = parse_argv(["say", "manager", "hello", "--image", "a.png", "--reply", "om_1", "--reply-in-thread"])
    assert parsed.command == "say"
    assert parsed.params["from_agent"] == "manager"
    assert parsed.params["message"] == "hello"
    assert parsed.params["image_path"] == "a.png"
    assert parsed.params["reply_to"] == "om_1"
    assert parsed.params["reply_in_thread"] is True


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
    print(f"\nfeishu_msg say tests: {passed}/{passed + failed} passed")
    return 0 if failed == 0 else 1


if __name__ == "__main__":
    raise SystemExit(main())
