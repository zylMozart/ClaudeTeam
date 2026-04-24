#!/usr/bin/env python3
"""Generate evidence table markdown from a smoke test chat window.

Usage:
    python3 scripts/smoke/evidence_table.py <chat_id> <start_iso> [--boss-open-id <id>]
    python3 scripts/smoke/evidence_table.py --self-test

Output (stdout): markdown table with boss messages matched to first agent
reply, one row per boss message. Columns: boss_msg_id, boss_time, agent,
reply_msg_id, reply_time, latency_ms, excerpt.

Heuristic: a boss message is one whose sender.id == --boss-open-id. If not
provided, we take messages with msg_type=text whose sender_type=="user" as
boss messages (works when only the boss is a human in the chat).
"""
import json
import re
import sys
from datetime import datetime, timezone, timedelta
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from _lark_chat import fetch_chat_messages, extract_text_content, parse_create_time_ms
from smoke_latency import _agent_marker


BJ_TZ = timezone(timedelta(hours=8))


def _is_boss(msg: dict, boss_open_id: str | None) -> bool:
    sender = msg.get("sender", {}) or {}
    if boss_open_id:
        return sender.get("id") == boss_open_id
    return sender.get("sender_type") == "user" and msg.get("msg_type") == "text"


def _detect_agent(text: str, agents: list[str]) -> str | None:
    for a in agents:
        if _agent_marker(a).search(text):
            return a
    return None


def _fmt_time(ms: int | None) -> str:
    if not ms:
        return "—"
    return datetime.fromtimestamp(ms / 1000, BJ_TZ).strftime("%H:%M:%S.%f")[:-3]


def _fmt_excerpt(text: str, n: int = 40) -> str:
    s = re.sub(r"\s+", " ", text).strip()
    return (s[:n] + "…") if len(s) > n else s


def build_evidence_table(
    messages: list[dict],
    agents: list[str],
    boss_open_id: str | None = None,
) -> str:
    """Return markdown table rows matching boss messages with first agent reply."""
    header = (
        "| # | boss_msg_id | boss_time (北京) | agent | reply_msg_id | "
        "reply_time (北京) | 延迟 (ms) | 消息摘要 |\n"
        "|---|-------------|------------------|-------|--------------|"
        "-------------------|-----------|-----------|"
    )
    rows: list[str] = []
    messages_sorted = sorted(messages, key=lambda m: parse_create_time_ms(m) or 0)
    for idx, m in enumerate(messages_sorted, 1):
        if not _is_boss(m, boss_open_id):
            continue
        boss_ct = parse_create_time_ms(m)
        boss_text = extract_text_content(m)
        mentioned = _detect_agent(boss_text, agents) or "(未指定)"
        reply_msg_id = "—"
        reply_ct = None
        latency = "—"
        for later in messages_sorted[idx:]:
            if _is_boss(later, boss_open_id):
                continue
            later_text = extract_text_content(later)
            detected = _detect_agent(later_text, agents)
            if detected and (mentioned == "(未指定)" or detected == mentioned):
                reply_msg_id = later.get("message_id", "—")
                reply_ct = parse_create_time_ms(later)
                if boss_ct and reply_ct:
                    latency = str(reply_ct - boss_ct)
                if mentioned == "(未指定)":
                    mentioned = detected
                break
        rows.append(
            f"| {len(rows)+1} | `{m.get('message_id','—')}` | "
            f"{_fmt_time(boss_ct)} | {mentioned} | `{reply_msg_id}` | "
            f"{_fmt_time(reply_ct)} | {latency} | {_fmt_excerpt(boss_text)} |"
        )
    if not rows:
        return header + "\n| — | (no boss messages detected in window) | | | | | | |"
    return header + "\n" + "\n".join(rows)


def _self_test() -> int:
    boss_id = "ou_boss_open_id"
    msgs = [
        {"message_id": "om_boss1", "create_time": "1713945600000",
         "sender": {"id": boss_id, "sender_type": "user"},
         "msg_type": "text",
         "content": json.dumps({"text": "@coder 写 hello world"})},
        {"message_id": "om_coder_reply", "create_time": "1713945605000",
         "sender": {"id": "bot_id", "sender_type": "app"},
         "msg_type": "text",
         "content": json.dumps({"text": "【coder】收到"})},
        {"message_id": "om_boss2", "create_time": "1713945700000",
         "sender": {"id": boss_id, "sender_type": "user"},
         "msg_type": "text",
         "content": json.dumps({"text": "@manager 汇总下"})},
        {"message_id": "om_mgr_reply", "create_time": "1713945702000",
         "sender": {"id": "bot_id", "sender_type": "app"},
         "msg_type": "text",
         "content": json.dumps({"text": "【manager】正在汇总"})},
        {"message_id": "om_boss3", "create_time": "1713945800000",
         "sender": {"id": boss_id, "sender_type": "user"},
         "msg_type": "text",
         "content": json.dumps({"text": "服务器好像卡了"})},
    ]
    agents = ["manager", "coder", "devops", "researcher"]
    table = build_evidence_table(msgs, agents, boss_open_id=boss_id)
    assert "om_boss1" in table, table
    assert "coder" in table
    assert "5000" in table, "latency row 1 should be 5000ms"
    assert "om_mgr_reply" in table
    assert "2000" in table, "latency row 2 should be 2000ms"
    assert "om_boss3" in table
    assert table.count("\n| ") >= 3, "expect 3 boss rows"

    empty = build_evidence_table([], agents, boss_open_id=boss_id)
    assert "no boss messages" in empty

    print("OK: evidence_table self-test passed (3 boss rows + empty)")
    return 0


def main() -> int:
    if len(sys.argv) > 1 and sys.argv[1] == "--self-test":
        return _self_test()
    if len(sys.argv) < 3:
        print(__doc__, file=sys.stderr)
        return 2
    chat_id = sys.argv[1]
    start_iso = sys.argv[2]
    boss_open_id = None
    if "--boss-open-id" in sys.argv:
        boss_open_id = sys.argv[sys.argv.index("--boss-open-id") + 1]
    agents = [
        "manager", "devops", "security", "toolsmith", "researcher",
        "qa_smoke", "docs_keeper", "architect", "coder", "docs_admin",
    ]
    msgs = fetch_chat_messages(chat_id, start_iso=start_iso)
    print(f"# e2e smoke evidence — chat `{chat_id}`\n")
    print(f"窗口起始：{start_iso} 北京时间\n")
    print(f"消息总数（窗口内）：{len(msgs)}\n")
    print(build_evidence_table(msgs, agents, boss_open_id=boss_open_id))
    return 0


if __name__ == "__main__":
    sys.exit(main())
