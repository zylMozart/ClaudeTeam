#!/usr/bin/env python3
"""No-live evidence builders for agent status signals.

Every function in this module accepts caller-provided data only. It does not
read tmux panes, Bitable, Feishu/Lark, docker, network, or pending queue files.
"""
from __future__ import annotations

from datetime import datetime, timezone


STALE_AFTER_SECS = 1800

_RUNNING_VALUES = {"running", "busy", "working", "in_progress", "进行中", "工作中"}
_DONE_VALUES = {"done", "completed", "complete", "finished", "已完成", "完成"}
_PENDING_VALUES = {"pending", "todo", "queued", "待处理"}
_CANCELLED_VALUES = {"cancelled", "canceled", "cancel", "已取消", "取消"}
_BLOCKED_VALUES = {"blocked", "阻塞", "blocked_waiting", "waiting_external"}
_IDLE_VALUES = {"idle", "ready", "waiting", "standby", "待命", "空闲"}
_OFFLINE_VALUES = {"offline", "missing", "not_found", "dead", "离线", "窗口不存在"}

_STRONG_LOG_TYPES = {"任务完成", "交付", "回报完成", "完成", "done", "completed", "delivery"}
_STRONG_LOG_MARKERS = ("验证通过", "交付路径", "测试通过", "已完成并回报", "delivered")
_WEAK_LOG_MARKERS = ("完成", "已完成", "已修复", "已输出", "done", "fixed")
_COMPLETION_SIGNAL_RANK = {"none": 0, "weak": 1, "strong": 2}


def _as_epoch(value):
    if value is None or value == "":
        return None
    if isinstance(value, (int, float)):
        return float(value)
    if isinstance(value, datetime):
        dt = value
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        return dt.timestamp()
    if isinstance(value, str):
        text = value.strip()
        if not text:
            return None
        try:
            return float(text)
        except ValueError:
            pass
        if text.endswith("Z"):
            text = text[:-1] + "+00:00"
        try:
            dt = datetime.fromisoformat(text)
        except ValueError:
            return None
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        return dt.timestamp()
    return None


def _now_epoch(now):
    value = _as_epoch(now)
    if value is not None:
        return value
    return datetime.now(timezone.utc).timestamp()


def _truthy(value):
    if isinstance(value, str):
        return value.strip().lower() in {"1", "true", "yes", "y", "on", "是"}
    return bool(value)


def _field(row, *names, default=None):
    row = row or {}
    for name in names:
        if name in row:
            return row[name]
    fields = row.get("fields")
    if isinstance(fields, dict):
        for name in names:
            if name in fields:
                return fields[name]
    return default


def _source_error_payload(prefix, data):
    return {
        f"{prefix}_source_error": True,
        "source_error": True,
        "source_error_detail": str(_field(data, "source_error_detail", "error", default="source_error")),
    }


def _task_state(status):
    text = str(status or "").strip().lower()
    if text in _RUNNING_VALUES:
        return "running"
    if text in _DONE_VALUES:
        return "done"
    if text in _PENDING_VALUES:
        return "pending"
    if text in _CANCELLED_VALUES:
        return "cancelled"
    if not text or text in {"none", "无"}:
        return "none"
    return "unknown"


def _status_state(status):
    text = str(status or "").strip().lower()
    if text in _BLOCKED_VALUES:
        return "blocked"
    if text in _RUNNING_VALUES:
        return "running"
    if text in _DONE_VALUES:
        return "done"
    if text in _IDLE_VALUES:
        return "idle"
    if text in _OFFLINE_VALUES:
        return "offline"
    return text


def _records(data):
    if data is None:
        return []
    if isinstance(data, list):
        return data
    if isinstance(data, dict):
        for key in ("tasks", "records", "rows", "items", "messages", "queue"):
            value = data.get(key)
            if isinstance(value, list):
                return value
        return [data]
    return []


def _matches_agent(row, agent):
    value = _field(row, "assignee", "agent", "Agent名称", "agent_name", "name", default="")
    if not value:
        return True
    return str(value) == str(agent)


def _record_time(row):
    return _as_epoch(_field(row, "updated_at", "更新时间", "time", "时间", "created_at", "queued_at"))


def _latest(rows):
    if not rows:
        return None
    return max(rows, key=lambda row: _record_time(row) or 0)


def _completion_rank(signal):
    return _COMPLETION_SIGNAL_RANK.get(str(signal or "none").lower(), 0)


def _merge_evidence(base, incoming):
    """Merge source evidence without weakening already-strong completion facts."""
    if not incoming:
        return
    current_signal = base.get("completion_signal")
    incoming_signal = incoming.get("completion_signal")
    keep_current_completion = (
        current_signal is not None
        and incoming_signal is not None
        and _completion_rank(incoming_signal) < _completion_rank(current_signal)
    )

    if not keep_current_completion:
        base.update(incoming)
        return

    completion_keys = {
        "completion_signal",
        "completed_at",
        "completed_at_s",
        "completion_at",
        "completion_at_s",
        "task_id",
        "current_task_id",
        "task_title",
    }
    for key, value in incoming.items():
        if key in completion_keys:
            continue
        base[key] = value


def build_agent_evidence(agent, sources, now=None):
    """Merge caller-provided source fixtures into one evidence dict."""
    sources = sources or {}
    evidence = {
        "agent": agent,
        "collected_at": _now_epoch(now),
        "sources": [],
    }

    collectors = (
        ("tmux_pane", collect_from_tmux_snapshot, sources.get("tmux_snapshot", sources.get("tmux_pane"))),
        ("task_tracker", collect_from_task_tracker, sources.get("task_tracker", sources.get("tasks"))),
        ("status_table", collect_from_status_table_row, sources.get("status_table_row", sources.get("status_table"))),
        ("workspace_log", collect_from_workspace_logs, sources.get("workspace_logs", sources.get("workspace_log"))),
        ("manager_inbox", collect_from_manager_inbox, sources.get("manager_inbox")),
        ("pending_queue", collect_from_pending_queue, sources.get("pending_queue")),
    )

    for source_name, collector, payload in collectors:
        if payload is None:
            continue
        if collector is collect_from_manager_inbox:
            collected = collector(payload, agent, completed_at=evidence.get("completed_at"))
        else:
            collected = collector(payload, agent)
        if collected:
            evidence["sources"].append(source_name)
            _merge_evidence(evidence, collected)

    return evidence


def collect_from_task_tracker(data, agent):
    """Extract task state evidence from an in-memory task tracker payload."""
    if isinstance(data, dict) and _truthy(data.get("source_error")):
        return _source_error_payload("task_tracker", data)

    agent_tasks = [row for row in _records(data) if _matches_agent(row, agent)]
    running = [row for row in agent_tasks if _task_state(_field(row, "status", "状态")) == "running"]
    done = [row for row in agent_tasks if _task_state(_field(row, "status", "状态")) == "done"]
    selected = _latest(running) or _latest(done) or _latest(agent_tasks)
    if not selected:
        return {"task_state": "none"}

    status = _field(selected, "status", "状态", default="")
    state = _task_state(status)
    evidence = {
        "task_id": _field(selected, "task_id", "id", "record_id", default="") or "",
        "task_title": _field(selected, "title", "任务标题", "name", default="") or "",
        "task_status": status,
        "task_state": state,
        "task_updated_at": _field(selected, "updated_at", "更新时间", default=None),
    }
    if state == "done":
        completed_at = _field(selected, "completed_at", "updated_at", "更新时间", default=None)
        evidence["completed_at"] = completed_at
        completed_at_s = _as_epoch(completed_at)
        if completed_at_s is not None:
            evidence["completed_at_s"] = completed_at_s
        evidence.setdefault("completion_signal", "strong")
    return evidence


def collect_from_status_table_row(row, agent):
    """Extract declared status evidence from a status table row fixture."""
    row = row or {}
    if _truthy(_field(row, "source_error", "status_table_source_error", default=False)):
        return _source_error_payload("status_table", row)

    status = _field(row, "状态", "status", "state", default="")
    updated_at = _field(row, "更新时间", "updated_at", default=None)
    now = _as_epoch(_field(row, "now", "_now", default=None))
    updated_at_s = _as_epoch(updated_at)
    stale = _truthy(_field(row, "status_table_stale", "stale", default=False))
    if now is not None and updated_at_s is not None:
        stale_after = _field(row, "stale_after_s", default=STALE_AFTER_SECS)
        stale = now - updated_at_s > int(stale_after)

    state = _status_state(status)
    evidence = {
        "status_table_state": status,
        "status_table_task": _field(row, "当前任务", "current_task", default="") or "",
        "status_table_blocker": _field(row, "阻塞原因", "blocker", "blocked_reason", default="") or "",
        "status_table_updated_at": updated_at,
        "status_table_stale": stale,
    }
    if updated_at_s is not None:
        evidence["status_table_updated_at_s"] = updated_at_s
    if stale:
        return evidence
    if state:
        evidence["status"] = state
    if state == "blocked":
        evidence["blocked"] = True
        evidence["blocked_reason"] = evidence["status_table_blocker"] or "status_table_blocked"
    return evidence


def collect_from_workspace_logs(rows, agent):
    """Extract completion hints from workspace log fixtures."""
    if isinstance(rows, dict) and _truthy(rows.get("source_error")):
        return _source_error_payload("workspace_log", rows)

    records = [row for row in _records(rows) if _matches_agent(row, agent)]
    latest = _latest(records)
    evidence = {
        "completion_signal": "none",
    }
    if not latest:
        return evidence

    log_type = str(_field(latest, "类型", "type", default="") or "")
    content = str(_field(latest, "内容", "content", "message", default="") or "")
    log_time = _field(latest, "时间", "time", "created_at", "updated_at", default=None)
    signal = "none"
    if log_type.lower() in _STRONG_LOG_TYPES or any(marker in content for marker in _STRONG_LOG_MARKERS):
        signal = "strong"
    elif any(marker in content for marker in _WEAK_LOG_MARKERS):
        signal = "weak"

    evidence.update({
        "latest_log_type": log_type,
        "latest_log_at": log_time,
        "latest_log_ref": _field(latest, "关联对象", "ref", "task_id", default="") or "",
        "completion_log_hit": signal != "none",
        "completion_signal": signal,
    })
    log_time_s = _as_epoch(log_time)
    if log_time_s is not None:
        evidence["latest_log_at_s"] = log_time_s
    if signal != "none":
        evidence["completed_at"] = log_time
        if log_time_s is not None:
            evidence["completed_at_s"] = log_time_s
        if not evidence.get("task_id") and evidence["latest_log_ref"]:
            evidence["task_id"] = evidence["latest_log_ref"]
    return evidence


def collect_from_manager_inbox(rows, agent, completed_at=None):
    """Extract manager-report evidence from in-memory inbox rows."""
    if isinstance(rows, dict) and _truthy(rows.get("source_error")):
        return _source_error_payload("manager_inbox", rows)

    completed_at_s = _as_epoch(completed_at)
    messages = _records(rows)
    manager_unread = 0
    report_rows = []
    for row in messages:
        recipient = _field(row, "收件人", "recipient", "to", default="")
        sender = _field(row, "发件人", "sender", "from", default="")
        if str(recipient) == "manager" and not _truthy(_field(row, "已读", "read", default=True)):
            manager_unread += 1
        if str(recipient) != "manager" or str(sender) != str(agent):
            continue
        msg_time_s = _as_epoch(_field(row, "时间", "time", "created_at", default=None))
        if completed_at_s is not None and (msg_time_s is None or msg_time_s < completed_at_s):
            continue
        report_rows.append(row)

    latest_report = _latest(report_rows)
    evidence = {
        "manager_inbox_from_agent_count": len(report_rows),
        "manager_unread_count": manager_unread,
        "already_reported": bool(latest_report),
    }
    if latest_report:
        reported_at = _field(latest_report, "时间", "time", "created_at", default=None)
        evidence["manager_reported_at"] = reported_at
        reported_at_s = _as_epoch(reported_at)
        if reported_at_s is not None:
            evidence["manager_reported_at_s"] = reported_at_s
        evidence["manager_report_message_id"] = _field(latest_report, "record_id", "msg_id", "id", default="") or ""
    return evidence


def collect_from_pending_queue(queue, agent):
    """Extract pending queue counts from an in-memory queue fixture."""
    if isinstance(queue, dict) and _truthy(queue.get("source_error")):
        return _source_error_payload("pending_queue", queue)

    if isinstance(queue, dict) and agent in queue and isinstance(queue[agent], list):
        messages = queue[agent]
    else:
        messages = _records(queue)

    pending_user = [msg for msg in messages if _truthy(_field(msg, "is_user_msg", default=False))]
    queued_times = [_as_epoch(_field(msg, "queued_at", default=None)) for msg in messages]
    queued_times = [value for value in queued_times if value is not None]
    evidence = {
        "pending_total": len(messages),
        "pending_user_messages": len(pending_user),
    }
    if queued_times:
        evidence["oldest_pending_at"] = min(queued_times)
    if str(agent) == "manager":
        evidence["pending_manager_messages"] = len(messages)
    return evidence


def collect_from_tmux_snapshot(snapshot, agent):
    """Extract pane evidence from a tmux snapshot fixture."""
    if isinstance(snapshot, dict) and agent in snapshot and isinstance(snapshot[agent], dict):
        snapshot = snapshot[agent]
    snapshot = snapshot or {}
    if _truthy(_field(snapshot, "source_error", "tmux_source_error", default=False)):
        return _source_error_payload("tmux", snapshot)

    pane_exists = _field(snapshot, "pane_exists", "exists", default=True)
    pane_tail = _field(snapshot, "pane_tail", "tail", default=None)
    if isinstance(pane_tail, list):
        pane_tail = "\n".join(str(line) for line in pane_tail[-20:])
    permission_prompt = _truthy(_field(snapshot, "permission_prompt", default=False))
    busy_marker = _truthy(_field(snapshot, "busy_marker", "busy", default=False))
    idle_marker = _truthy(_field(snapshot, "idle_marker", "idle", "is_idle", default=False))
    pane_state = _field(snapshot, "pane_state", "state", default="")
    if pane_exists is False:
        pane_state = "offline"
    elif permission_prompt:
        pane_state = "blocked"
    elif not pane_state:
        if busy_marker:
            pane_state = "busy"
        elif idle_marker:
            pane_state = "idle"
        else:
            pane_state = "unknown"

    return {
        "pane_exists": pane_exists,
        "pane_state": str(pane_state),
        "pane_tail": pane_tail,
        "idle_marker": idle_marker,
        "busy_marker": busy_marker,
        "lazy_banner": _truthy(_field(snapshot, "lazy_banner", default=False)),
        "permission_prompt": permission_prompt,
    }
