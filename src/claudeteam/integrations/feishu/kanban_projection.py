"""Pure helpers for Kanban projection row shaping."""
from __future__ import annotations

from datetime import datetime
from typing import Any, Iterable


KANBAN_FIELD_NAMES = ["任务ID", "标题", "状态", "负责人", "Agent当前状态", "Agent当前任务"]
KANBAN_TABLE_FIELDS = (
    {"name": "任务ID", "type": "text"},
    {"name": "标题", "type": "text"},
    {"name": "状态", "type": "text"},
    {"name": "负责人", "type": "text"},
    {"name": "Agent当前状态", "type": "text"},
    {"name": "Agent当前任务", "type": "text"},
    {"name": "任务更新时间", "type": "date_time"},
    {"name": "Agent状态更新", "type": "date_time"},
)


def extract_text(value: Any) -> str:
    if isinstance(value, list):
        return value[0].get("text", "") if value else ""
    return str(value) if value else ""


def to_ms(iso_str: str) -> int:
    try:
        return int(datetime.fromisoformat(iso_str).timestamp() * 1000)
    except Exception:
        return 0


def chunks(items: list[Any], size: int) -> Iterable[list[Any]]:
    for i in range(0, len(items), size):
        yield items[i:i + size]


def build_agent_status_map(items: list[dict[str, Any]]) -> dict[str, dict[str, Any]]:
    result: dict[str, dict[str, Any]] = {}
    seen: set[str] = set()
    for item in items:
        fields = item.get("fields", {})
        agent = extract_text(fields.get("Agent名称", ""))
        if not agent or agent in seen:
            continue
        seen.add(agent)
        updated_ms = fields.get("更新时间", 0)
        if isinstance(updated_ms, list):
            updated_ms = updated_ms[0].get("value", 0) if updated_ms else 0
        result[agent] = {
            "状态": extract_text(fields.get("状态", "")),
            "当前任务": extract_text(fields.get("当前任务", "")),
            "更新时间": updated_ms,
        }
    return result


def build_kanban_table_fields() -> list[dict[str, Any]]:
    return [dict(field) for field in KANBAN_TABLE_FIELDS]


def extract_kanban_record_ids(items: list[dict[str, Any]]) -> list[str]:
    return [item["record_id"] for item in items]


def build_kanban_rows(tasks: list[dict[str, Any]], agent_status: dict[str, dict[str, Any]]) -> list[list[Any]]:
    rows = []
    for task in tasks:
        assignee = task["assignee"]
        ast = agent_status.get(assignee, {})
        rows.append([
            task["task_id"],
            task["title"],
            task["status"],
            assignee,
            ast.get("状态", "未知"),
            ast.get("当前任务", ""),
        ])
    return rows
