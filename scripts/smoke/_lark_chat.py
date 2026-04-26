#!/usr/bin/env python3
"""Shared helper: fetch chat messages via lark-cli.

Thin wrapper around `npx @larksuite/cli im +chat-messages-list --as bot`.
Returns parsed JSON list of messages sorted by create_time ASC.
"""
import json
import subprocess
from typing import Optional


def fetch_chat_messages(
    chat_id: str,
    start_iso: Optional[str] = None,
    end_iso: Optional[str] = None,
    page_size: int = 50,
    max_pages: int = 20,
    lark_cli: str = "npx @larksuite/cli",
    timeout: int = 40,
) -> list[dict]:
    """Return list of messages in chat sorted ASC by create_time.

    start_iso / end_iso are optional ISO timestamps ("2026-04-24T10:00:00+08:00").
    """
    messages: list[dict] = []
    page_token = ""
    for _ in range(max_pages):
        args = lark_cli.split() + [
            "im", "+chat-messages-list",
            "--chat-id", chat_id,
            "--sort", "asc",
            "--page-size", str(page_size),
            "--as", "bot",
            "--format", "json",
        ]
        if start_iso:
            args += ["--start", start_iso]
        if end_iso:
            args += ["--end", end_iso]
        if page_token:
            args += ["--page-token", page_token]
        r = subprocess.run(args, capture_output=True, text=True, timeout=timeout)
        if r.returncode != 0:
            raise RuntimeError(f"lark-cli failed: {r.stderr[:500]}")
        try:
            data = json.loads(r.stdout)
        except json.JSONDecodeError as e:
            raise RuntimeError(f"lark-cli output not JSON: {e}") from e
        batch = data.get("messages", data.get("items", []))
        messages.extend(batch)
        page_token = data.get("page_token", "") or ""
        if not page_token or not batch:
            break
    return messages


def extract_text_content(msg: dict) -> str:
    """Pull flat text out of a feishu message dict, tolerating card/post variants."""
    raw = msg.get("content") or msg.get("body", {}).get("content", "")
    if not raw:
        return ""
    if isinstance(raw, str):
        try:
            obj = json.loads(raw)
        except (json.JSONDecodeError, TypeError):
            return raw
    else:
        obj = raw
    if isinstance(obj, dict):
        if "text" in obj:
            return str(obj["text"])
        if "elements" in obj:
            parts = []
            for block in obj.get("elements", []):
                for el in block if isinstance(block, list) else [block]:
                    if isinstance(el, dict):
                        parts.append(el.get("text", "") or el.get("content", ""))
            return " ".join(p for p in parts if p)
    return str(obj)


def parse_create_time_ms(msg: dict) -> Optional[int]:
    """Parse create_time to integer milliseconds since epoch, None if missing."""
    ct = msg.get("create_time")
    if ct is None:
        return None
    try:
        ct_int = int(ct)
    except (TypeError, ValueError):
        return None
    if ct_int < 10**11:
        return ct_int * 1000
    return ct_int


def _self_test() -> int:
    sample_msgs = [
        {"message_id": "om_a", "create_time": "1713945600000",
         "content": json.dumps({"text": "hello"}), "msg_type": "text"},
        {"message_id": "om_b", "create_time": 1713945610,
         "content": json.dumps({"text": "world"}), "msg_type": "text"},
        {"message_id": "om_c", "create_time": "1713945620000",
         "body": {"content": json.dumps({"text": "card"})}, "msg_type": "interactive"},
    ]
    assert extract_text_content(sample_msgs[0]) == "hello"
    assert extract_text_content(sample_msgs[1]) == "world"
    assert extract_text_content(sample_msgs[2]) == "card"
    assert parse_create_time_ms(sample_msgs[0]) == 1713945600000
    assert parse_create_time_ms(sample_msgs[1]) == 1713945610000
    assert parse_create_time_ms({}) is None
    assert parse_create_time_ms({"create_time": "bad"}) is None
    print("OK: _lark_chat self-test passed (3 extract + 4 parse)")
    return 0


if __name__ == "__main__":
    import sys
    if len(sys.argv) > 1 and sys.argv[1] == "--self-test":
        sys.exit(_self_test())
    print("use as: from _lark_chat import fetch_chat_messages, extract_text_content, parse_create_time_ms")
    sys.exit(0)
