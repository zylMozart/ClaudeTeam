"""Messaging helper/service functions for Feishu command handlers."""
from __future__ import annotations

from claudeteam.runtime.config import AGENTS
from claudeteam.storage import local_facts
from claudeteam.messaging.renderer import render_feishu_markdown, render_inbox_text, render_log_text


def sanitize_agent_message(text: str) -> str:
    """Remove Codex CLI spawn command fragments accidentally mixed into messages."""
    return render_inbox_text(text)


def build_system_card(content: str, template: str = "grey") -> dict:
    """系统消息卡片（给 slash 命令的文本回显用），不带 sender · role 标签。"""
    content = render_feishu_markdown(content)
    return {
        "config": {"wide_screen_mode": True},
        "header": {
            "template": template,
            "title": {"tag": "plain_text", "content": "🛠️ 系统消息"},
        },
        "elements": [{"tag": "markdown", "content": content}],
    }


def build_card(from_agent, to_agent, content, priority="中"):
    """构建飞书消息卡片 JSON"""
    content = render_feishu_markdown(content)
    info = AGENTS.get(from_agent, {"role": "?", "emoji": "🤖", "color": "grey"})
    emoji = info["emoji"]
    role = info["role"]
    color = info.get("color", "grey")

    if to_agent and to_agent != "*":
        title = f"{emoji} {from_agent} · {role} → @{to_agent}"
    else:
        title = f"{emoji} {from_agent} · {role}"

    pri_tag = {"高": "🔴 ", "中": "", "低": "🟢 "}.get(priority, "")

    return {
        "config": {"wide_screen_mode": True},
        "header": {
            "template": color,
            "title": {"tag": "plain_text", "content": title},
        },
        "elements": [
            {"tag": "markdown", "content": f"{pri_tag}{content}"},
        ],
    }


def ws_log(agent, log_type, content, ref=""):
    """Write local workspace audit log for core state/evidence."""
    content = render_log_text(content)
    local_facts.append_log(agent, log_type, content, ref)


from claudeteam.commands.workspace import (  # noqa: E402
    cmd_log as cmd_log,
    cmd_workspace as cmd_workspace,
    cmd_inbox as cmd_inbox,
)


def mark_local_read(record_id):
    """Mark local inbox message as read and keep legacy CLI output contract."""
    if local_facts.mark_read(record_id):
        print(f"✅ 已标记本地已读: {record_id}")
        return True
    return False


def upsert_local_status(agent_name, status, task, blocker=""):
    """Persist agent status to local facts (core source of truth)."""
    local_facts.upsert_status(agent_name, status, task, blocker)


def record_local_send(to_agent, from_agent, message, priority="中", task_id=""):
    """Persist outbound message to local inbox and return local_id/message payload."""
    message = sanitize_agent_message(message)
    actual_message = f"[{task_id}] {message}" if task_id else message
    local_id = local_facts.append_message(
        to_agent,
        from_agent,
        actual_message,
        priority,
        task_id=task_id,
    )
    return local_id, actual_message


def record_local_direct(to_agent, from_agent, message):
    """Persist direct message local facts and optional manager CC copy."""
    message = sanitize_agent_message(message)
    local_id = local_facts.append_message(to_agent, from_agent, message, "中")

    cc_local_id = None
    cc_content = ""
    if to_agent != "manager" and from_agent != "manager":
        cc_content = f"[抄送] {from_agent}→{to_agent}: {message}"
        cc_local_id = local_facts.append_message("manager", from_agent, cc_content, "低")

    return {
        "message": message,
        "local_id": local_id,
        "cc_local_id": cc_local_id,
        "cc_content": cc_content,
    }
