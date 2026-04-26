"""CLI command handlers for workspace log, inbox, and audit log operations."""
from __future__ import annotations

import time

from claudeteam.messaging.renderer import render_log_text
from claudeteam.storage import local_facts


def _sanitize(text: str) -> str:
    from claudeteam.messaging.service import sanitize_agent_message
    return sanitize_agent_message(text)


def cmd_log(agent_name, log_type, content, ref=""):
    local_id = local_facts.append_log(agent_name, log_type, render_log_text(content), ref)
    print(f"✅ [{log_type}] 已写入 {agent_name} 本地工作空间日志 [local_id: {local_id}]")


def cmd_workspace(agent_name):
    items = local_facts.list_logs(agent_name, limit=20)
    print(f"📁 {agent_name} 本地工作空间日志 (最近 {len(items)} 条):\n")
    for rec in items:
        t = rec.get("created_at", 0)
        ts = time.strftime("%m-%d %H:%M", time.localtime(t / 1000)) if isinstance(t, (int, float)) else "?"
        lt = rec.get("type", "?")
        c = rec.get("content", "")
        ref = rec.get("ref", "")
        print(f"  [{ts}] {lt:8} {c[:10000]}")
        if ref:
            print(f"           → {ref}")


def cmd_inbox(agent_name):
    unread = local_facts.list_messages(agent_name, unread_only=True)
    if not unread:
        print(f"📭 {agent_name} 暂无未读消息")
        return
    print(f"📬 {agent_name} 有 {len(unread)} 条未读消息:\n")
    for rec in unread:
        rid = rec["local_id"]
        t = rec.get("created_at", 0)
        ts = time.strftime("%m-%d %H:%M", time.localtime(t / 1000)) if isinstance(t, (int, float)) else "?"
        frm = rec.get("from", "?")
        pri = rec.get("priority", "?")
        content = _sanitize(rec.get("content", ""))
        print(f"── [{ts}] 来自 {frm} [优先级:{pri}]")
        print(f"   {content}")
        print(f"   标记已读: python3 scripts/feishu_msg.py read {rid}")
        print()
