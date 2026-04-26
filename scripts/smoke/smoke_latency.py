#!/usr/bin/env python3
"""Measure boss→agent reply latency for an e2e smoke case.

Usage:
    python3 scripts/smoke/smoke_latency.py <chat_id> <boss_msg_id> <agent_name>
    python3 scripts/smoke/smoke_latency.py --self-test

Output (stdout, single JSON line):
    {"boss_msg_id": ..., "boss_create_time": ..., "agent": ...,
     "reply_msg_id": ..., "reply_create_time": ..., "latency_ms": ...}

Agent reply detection:
    1. Agent replies go through `feishu_msg.py say` which wraps content with
       marker "【<agent>】" or "@<agent>" prefix in text.
    2. We scan messages AFTER the boss message and pick the first whose text
       contains the agent name as an identity marker (【agent】, <agent>:, or
       the literal agent name as the first identifier in the reply).
    3. If no reply found within the fetched window, latency_ms = -1.
"""
import json
import re
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from _lark_chat import fetch_chat_messages, extract_text_content, parse_create_time_ms


_AGENT_PATTERNS = [
    r"【\s*{a}\s*[】:：]",
    r"\[{a}\]",
    r"\b{a}\s*[:：]",
    r"@{a}\b",
    r"姓名\s*[:：]\s*{a}\b",
]


def _agent_marker(agent: str) -> re.Pattern:
    safe = re.escape(agent)
    alt = "|".join(p.format(a=safe) for p in _AGENT_PATTERNS)
    return re.compile(alt, re.IGNORECASE)


def measure_latency(
    messages: list[dict], boss_msg_id: str, agent: str
) -> dict:
    """Locate boss msg + first agent reply in ASC-sorted message list."""
    marker = _agent_marker(agent)
    boss_idx = None
    for i, m in enumerate(messages):
        if m.get("message_id") == boss_msg_id:
            boss_idx = i
            break
    if boss_idx is None:
        return {
            "boss_msg_id": boss_msg_id, "boss_create_time": None,
            "agent": agent, "reply_msg_id": None, "reply_create_time": None,
            "latency_ms": -1, "error": "boss_msg_id not found in message window",
        }
    boss_ct = parse_create_time_ms(messages[boss_idx])
    for m in messages[boss_idx + 1:]:
        text = extract_text_content(m)
        if not text:
            continue
        if not marker.search(text):
            continue
        reply_ct = parse_create_time_ms(m)
        latency = reply_ct - boss_ct if (reply_ct and boss_ct) else -1
        return {
            "boss_msg_id": boss_msg_id, "boss_create_time": boss_ct,
            "agent": agent, "reply_msg_id": m.get("message_id"),
            "reply_create_time": reply_ct, "latency_ms": latency,
        }
    return {
        "boss_msg_id": boss_msg_id, "boss_create_time": boss_ct,
        "agent": agent, "reply_msg_id": None, "reply_create_time": None,
        "latency_ms": -1, "error": f"no reply from agent={agent} found after boss msg",
    }


def _self_test() -> int:
    msgs = [
        {"message_id": "om_noise_before",
         "create_time": "1713945000000",
         "content": json.dumps({"text": "earlier chatter"}),
         "msg_type": "text"},
        {"message_id": "om_boss",
         "create_time": "1713945600000",
         "content": json.dumps({"text": "@coder 帮我写个 hello world"}),
         "msg_type": "text"},
        {"message_id": "om_noise",
         "create_time": "1713945601000",
         "content": json.dumps({"text": "某 bot 回声"}),
         "msg_type": "text"},
        {"message_id": "om_coder_reply",
         "create_time": "1713945605500",
         "content": json.dumps({"text": "【coder】收到，开始写"}),
         "msg_type": "text"},
        {"message_id": "om_manager_reply",
         "create_time": "1713945610000",
         "content": json.dumps({"text": "【manager】已分派"}),
         "msg_type": "text"},
    ]
    r = measure_latency(msgs, "om_boss", "coder")
    assert r["reply_msg_id"] == "om_coder_reply", r
    assert r["latency_ms"] == 5500, r
    assert r["boss_create_time"] == 1713945600000, r

    r2 = measure_latency(msgs, "om_boss", "manager")
    assert r2["reply_msg_id"] == "om_manager_reply", r2
    assert r2["latency_ms"] == 10000, r2

    r3 = measure_latency(msgs, "om_boss", "devops")
    assert r3["reply_msg_id"] is None, r3
    assert r3["latency_ms"] == -1, r3

    r4 = measure_latency(msgs, "om_missing", "coder")
    assert r4["latency_ms"] == -1, r4
    assert "not found" in r4.get("error", ""), r4

    msgs2 = [
        {"message_id": "om_boss2", "create_time": "1713946000000",
         "content": json.dumps({"text": "test"}), "msg_type": "text"},
        {"message_id": "om_reply_alt", "create_time": "1713946001000",
         "content": json.dumps({"text": "[devops] 已响应"}),
         "msg_type": "text"},
    ]
    r5 = measure_latency(msgs2, "om_boss2", "devops")
    assert r5["latency_ms"] == 1000, r5

    print("OK: smoke_latency self-test passed (5 scenarios)")
    return 0


def main() -> int:
    if len(sys.argv) > 1 and sys.argv[1] == "--self-test":
        return _self_test()
    if len(sys.argv) != 4:
        print(__doc__, file=sys.stderr)
        return 2
    chat_id, boss_msg_id, agent = sys.argv[1], sys.argv[2], sys.argv[3]
    msgs = fetch_chat_messages(chat_id)
    result = measure_latency(msgs, boss_msg_id, agent)
    print(json.dumps(result, ensure_ascii=False))
    return 0 if result.get("latency_ms", -1) >= 0 else 1


if __name__ == "__main__":
    sys.exit(main())
