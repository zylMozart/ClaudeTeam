#!/usr/bin/env python3
"""Pure agent status signal helpers.

This module intentionally has no tmux, Bitable, network, or filesystem access.
Callers pass already-collected evidence and decide whether/how to send alerts.
"""
from __future__ import annotations

from datetime import datetime, timezone


STATE_UNKNOWN = "unknown"
STATE_OFFLINE = "offline"
STATE_BLOCKED = "blocked"
STATE_BUSY = "busy"
STATE_IDLE = "idle"

STRONG_COMPLETION_GRACE_SECS = 180
WEAK_COMPLETION_GRACE_SECS = 480
COMPLETION_ALERT_DEDUPE_SECS = 1800

_OFFLINE_VALUES = {"offline", "window_gone", "missing", "not_found", "dead", "离线", "窗口不存在"}
_BLOCKED_VALUES = {"blocked", "阻塞", "blocked_waiting", "waiting_external"}
_BUSY_VALUES = {"busy", "running", "working", "thinking", "进行中", "工作中", "思考中"}
_IDLE_VALUES = {"idle", "ready", "waiting", "standby", "待命", "空闲"}
_DONE_VALUES = {"done", "completed", "complete", "finished", "已完成", "完成"}


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


def _truthy(value):
    if isinstance(value, str):
        return value.strip().lower() in {"1", "true", "yes", "y", "on", "是"}
    return bool(value)


def _count(value):
    if value is None:
        return 0
    if isinstance(value, bool):
        return int(value)
    if isinstance(value, (int, float)):
        return int(value)
    if isinstance(value, (list, tuple, set, dict)):
        return len(value)
    try:
        return int(value)
    except (TypeError, ValueError):
        return 0


def _first_text(evidence, *keys):
    for key in keys:
        value = evidence.get(key)
        if value is not None:
            return str(value).strip()
    return ""


def _state_result(state, reason, *, evidence=None, signals=None):
    return {
        "state": state,
        "reason": reason,
        "signals": list(signals or []),
        "agent": (evidence or {}).get("agent") or (evidence or {}).get("name") or "",
    }


def normalize_agent_state(evidence, now=None):
    """Normalize collected evidence into one of unknown/offline/blocked/busy/idle.

    The function is conservative: missing evidence returns ``unknown`` rather
    than assuming idle. Pending user messages also prevent an idle verdict.
    """
    evidence = evidence or {}
    if _truthy(evidence.get("source_error")):
        return _state_result(STATE_UNKNOWN, "source_error", evidence=evidence, signals=["source_error"])
    table_state = _first_text(evidence, "task_state", "task_status", "status", "state").lower()
    raw_state = _first_text(evidence, "state", "status", "pane_status", "task_status").lower()
    pane_state = _first_text(evidence, "pane_state").lower()
    task_state = _first_text(evidence, "task_state").lower()
    signals = []

    if evidence.get("pane_tail") is None and "pane_tail" in evidence:
        return _state_result(STATE_OFFLINE, "pane_tail_missing", evidence=evidence, signals=["pane_missing"])
    if evidence.get("pane_exists") is False or evidence.get("window_exists") is False:
        return _state_result(STATE_OFFLINE, "pane_missing", evidence=evidence, signals=["pane_missing"])
    if evidence.get("alive") is False or evidence.get("process_alive") is False:
        return _state_result(STATE_OFFLINE, "process_not_alive", evidence=evidence, signals=["process_dead"])
    state_candidates = [value for value in (pane_state, table_state, raw_state) if value]
    for state in state_candidates:
        if state in _OFFLINE_VALUES:
            return _state_result(STATE_OFFLINE, f"state={state}", evidence=evidence, signals=["offline_state"])

    for state in (table_state, pane_state, raw_state):
        if _truthy(evidence.get("blocked")) or state in _BLOCKED_VALUES:
            reason = evidence.get("blocker") or evidence.get("blocked_reason") or f"state={state or 'blocked'}"
            return _state_result(STATE_BLOCKED, str(reason), evidence=evidence, signals=["blocked"])
    if _truthy(evidence.get("blocked")):
        reason = evidence.get("blocker") or evidence.get("blocked_reason") or "blocked"
        return _state_result(STATE_BLOCKED, str(reason), evidence=evidence, signals=["blocked"])

    pending_user = _count(
        evidence.get("pending_user_messages", evidence.get("pending_user_msg_count"))
    )
    if pending_user > 0:
        return _state_result(
            STATE_BUSY,
            f"pending_user_messages={pending_user}",
            evidence=evidence,
            signals=["pending_user_messages"],
        )

    busy_state = ""
    for state in (table_state, pane_state, raw_state):
        if state in _BUSY_VALUES:
            busy_state = state
            break
    if _truthy(evidence.get("busy")) or _truthy(evidence.get("busy_marker")) or busy_state:
        reason = f"state={busy_state or 'busy'}"
        signals = ["busy"]
        if task_state in _DONE_VALUES:
            reason = f"conflict: task_state={task_state} pane_state={pane_state or busy_state or 'busy'}"
            signals.append("completion_busy_conflict")
        result = _state_result(STATE_BUSY, reason, evidence=evidence, signals=signals)
        if task_state in _DONE_VALUES:
            result["conflict"] = True
        return result

    effective_state = pane_state or raw_state
    if (
        _truthy(evidence.get("idle"))
        or evidence.get("is_idle") is True
        or effective_state in _IDLE_VALUES
    ):
        return _state_result(STATE_IDLE, f"state={effective_state or 'idle'}", evidence=evidence, signals=["idle"])

    return _state_result(STATE_UNKNOWN, "insufficient_evidence", evidence=evidence, signals=["unknown"])


def _completion_key(evidence):
    agent = evidence.get("agent") or evidence.get("name") or "unknown"
    task_id = evidence.get("task_id") or evidence.get("current_task_id")
    if task_id:
        return f"{agent}:{task_id}"
    title = evidence.get("task_title") or evidence.get("title") or ""
    completed_at = evidence.get("completed_at") or evidence.get("completion_at") or ""
    return f"{agent}:{title}:{completed_at}"


def _is_done(evidence):
    if _truthy(evidence.get("completed")) or _truthy(evidence.get("completion_detected")):
        return True
    completion_signal = _first_text(evidence, "completion_signal", "completion_strength").lower()
    if completion_signal in {"strong", "weak"}:
        return True
    status = _first_text(evidence, "task_status", "task_state", "status", "state").lower()
    return status in _DONE_VALUES


def detect_completion_without_report(evidence, now=None, dedupe_state=None):
    """Detect completed work that has not been reported to manager.

    Returns a dict with ``alert`` and metadata. ``dedupe_state`` is an optional
    caller-owned dict; when provided, alert keys are recorded there so repeated
    calls do not produce duplicate alerts.
    """
    evidence = dict(evidence or {})
    dedupe_state = dedupe_state if dedupe_state is not None else {}
    now_ts = _as_epoch(now)
    if now_ts is None:
        now_ts = datetime.now(timezone.utc).timestamp()

    key = _completion_key(evidence)
    previous = dedupe_state.get(key)
    if previous is not None and evidence.get("last_alert_at_s") is None and evidence.get("last_alert_at") is None:
        if isinstance(previous, dict):
            evidence["last_alert_at_s"] = previous.get("last_alert_at") or previous.get("first_alerted_at")
        else:
            evidence["last_alert_at_s"] = previous

    signal = evaluate_completion_report_signal(evidence, now=now_ts)
    should_alert = bool(signal.get("should_remind"))
    result = {
        "alert": should_alert,
        "should_alert": should_alert,
        "should_remind": should_alert,
        "reason": signal.get("reason", ""),
        "dedupe_key": signal.get("dedupe_key") or key,
        "age_secs": signal.get("age_secs"),
        "grace_secs": signal.get("grace_secs"),
        "agent": signal.get("agent") or evidence.get("agent") or evidence.get("name") or "",
        "task_id": signal.get("task_id") or evidence.get("task_id") or evidence.get("current_task_id") or "",
        "state": signal.get("state"),
    }

    if should_alert:
        dedupe_state[key] = {
            "first_alerted_at": now_ts,
            "last_alert_at": now_ts,
            "age_secs": signal.get("age_secs"),
        }
    return result


def classify_agent_status(evidence, now=None):
    """Compatibility wrapper for the P0.2 status contract tests."""
    return normalize_agent_state(evidence, now)


def _completion_grace_secs(evidence):
    explicit = _count(evidence.get("completion_report_grace_secs"))
    if explicit > 0:
        return explicit
    strength = _first_text(evidence, "completion_signal", "completion_strength").lower()
    if strength == "weak":
        return WEAK_COMPLETION_GRACE_SECS
    return STRONG_COMPLETION_GRACE_SECS


def evaluate_completion_report_signal(evidence, now=None):
    """Return reminder signal for completed work that has not been reported."""
    evidence = evidence or {}
    now_ts = _as_epoch(now)
    if now_ts is None:
        now_ts = datetime.now(timezone.utc).timestamp()

    if _truthy(evidence.get("manager_inbox_source_error")):
        return {
            "state": "unknown",
            "should_remind": False,
            "reason": "manager_inbox_source_error",
            "source_error": True,
        }

    reported_at = _as_epoch(evidence.get("manager_reported_at_s") or evidence.get("manager_reported_at"))
    if reported_at is None:
        reported_at = _as_epoch(evidence.get("reported_at_s") or evidence.get("reported_at"))
    if reported_at is not None or _truthy(evidence.get("reported")) or _truthy(evidence.get("manager_reported")):
        return {"state": "resolved", "resolved": True, "should_remind": False, "reason": "already_reported"}

    if not _is_done(evidence):
        return {"state": "open", "should_remind": False, "reason": "not_completed"}

    completed_at = _as_epoch(
        evidence.get("completed_at_s")
        or evidence.get("completion_at_s")
        or evidence.get("completed_at")
        or evidence.get("completion_at")
    )
    if completed_at is None:
        return {"state": "unknown", "should_remind": False, "reason": "missing_completed_at"}

    grace = _completion_grace_secs(evidence)
    age = max(0, int(now_ts - completed_at))
    if age < grace:
        return {
            "state": "grace",
            "should_remind": False,
            "reason": "within_grace",
            "age_secs": age,
            "grace_secs": grace,
        }

    last_alert_at = _as_epoch(evidence.get("last_alert_at_s") or evidence.get("last_alert_at"))
    if last_alert_at is not None and now_ts - last_alert_at < COMPLETION_ALERT_DEDUPE_SECS:
        return {
            "state": "deduped",
            "should_remind": False,
            "reason": "dedupe_window",
            "age_secs": age,
            "dedupe_secs": COMPLETION_ALERT_DEDUPE_SECS,
        }

    return {
        "state": "pending_report",
        "should_remind": True,
        "reason": "completion_without_report",
        "age_secs": age,
        "grace_secs": grace,
        "dedupe_key": _completion_key(evidence),
        "agent": evidence.get("agent") or evidence.get("name") or "",
        "task_id": evidence.get("task_id") or evidence.get("current_task_id") or "",
    }
