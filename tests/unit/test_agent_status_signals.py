from __future__ import annotations

import dataclasses
import inspect

import pytest


pytestmark = pytest.mark.unit


agent_status_signals = pytest.importorskip(
    "agent_status_signals",
    reason=(
        "P0.2 contract scaffold: waiting for coder's pure "
        "agent_status_signals implementation"
    ),
)


NOW = 1_800_000_000


def _to_mapping(value):
    if dataclasses.is_dataclass(value):
        return dataclasses.asdict(value)
    if isinstance(value, dict):
        return dict(value)
    if isinstance(value, str):
        return {"status": value}
    attrs = {}
    for name in (
        "status",
        "state",
        "reason",
        "conflict",
        "should_remind",
        "should_alert",
        "resolved",
        "dedupe_key",
    ):
        if hasattr(value, name):
            attrs[name] = getattr(value, name)
    return attrs


def _find_function(module, names):
    for name in names:
        fn = getattr(module, name, None)
        if callable(fn):
            return fn
    pytest.skip(
        "P0.2 contract scaffold: expected one of these pure functions: "
        + ", ".join(names)
    )


def _call_flex(fn, payload, *, now=NOW):
    """Call candidate implementations while keeping the contract explicit.

    Preferred signature is either fn(payload, now=...) or fn(**payload, now=...).
    This adapter only exists so the test scaffold can land before coder's
    exact function signature is finalized.
    """
    signature = inspect.signature(fn)
    params = signature.parameters
    try:
        if any(p.kind == p.VAR_KEYWORD for p in params.values()):
            return fn(**payload, now=now)
        if "evidence" in params:
            return fn(evidence=payload, now=now)
        if "signals" in params:
            return fn(signals=payload, now=now)
        if "payload" in params:
            return fn(payload=payload, now=now)
        if len(params) == 1:
            return fn(payload)
        return fn(payload, now=now)
    except TypeError as exc:
        raise AssertionError(
            f"{fn.__name__} must accept a pure signal payload plus optional now; "
            f"signature={signature}"
        ) from exc


@pytest.fixture
def classify_status():
    fn = _find_function(
        agent_status_signals,
        (
            "classify_agent_status",
            "classify_status",
            "detect_agent_status",
            "status_from_signals",
            "normalize_agent_state",
        ),
    )

    def _classify(**overrides):
        payload = {
            "agent": "coder",
            "source_error": False,
            "pending_user_messages": 0,
            "pane_state": "idle",
            "pane_status": "idle",
            "status": "idle",
            "task_state": "none",
            "task_status": "none",
            "last_activity_s": NOW - 60,
        }
        payload.update(overrides)
        if "pane_state" in overrides:
            payload["pane_status"] = overrides["pane_state"]
            if "status" not in overrides:
                payload["status"] = overrides["pane_state"]
        if "task_state" in overrides:
            if "task_status" not in overrides:
                payload["task_status"] = overrides["task_state"]
        return _to_mapping(_call_flex(fn, payload))

    return _classify


@pytest.fixture
def completion_signal():
    fn = _find_function(
        agent_status_signals,
        (
            "evaluate_completion_report_signal",
            "evaluate_unreported_completion",
            "detect_unreported_completion",
            "completion_report_signal",
            "detect_completion_without_report",
        ),
    )

    def _evaluate(**overrides):
        payload = {
            "agent": "coder",
            "task_id": "T-P0-2",
            "task_state": "done",
            "task_status": "done",
            "completion_signal": "strong",
            "completed_at_s": NOW - 181,
            "completed_at": NOW - 181,
            "manager_reported_at_s": None,
            "manager_reported_at": None,
            "last_alert_at_s": None,
        }
        payload.update(overrides)
        if "completed_at_s" in overrides:
            payload["completed_at"] = overrides["completed_at_s"]
        if "manager_reported_at_s" in overrides:
            payload["manager_reported_at"] = overrides["manager_reported_at_s"]
        if "task_state" in overrides:
            payload["task_status"] = overrides["task_state"]
        return _to_mapping(_call_flex(fn, payload))

    return _evaluate


def _status(result):
    return result.get("status") or result.get("state")


def _reason(result):
    return str(result.get("reason") or result.get("detail") or "")


def _should_remind(result):
    if "should_remind" in result:
        return bool(result["should_remind"])
    if "should_alert" in result:
        return bool(result["should_alert"])
    if "alert" in result:
        return bool(result["alert"])
    if "remind" in result:
        return bool(result["remind"])
    if _status(result) in {"pending_report", "remind", "alert"}:
        return True
    return False


def _resolved(result):
    return bool(
        result.get("resolved")
        or _status(result) == "resolved"
        or result.get("reason") == "already_reported"
    )


def test_pending_user_message_is_not_idle(classify_status):
    result = classify_status(pending_user_messages=1, pane_state="idle")

    assert _status(result) != "idle", result
    assert "pending" in _reason(result).lower() or "user" in _reason(result).lower()


def test_source_error_becomes_unknown(classify_status):
    result = classify_status(source_error=True, source="status_table")

    assert _status(result) == "unknown", result


@pytest.mark.parametrize(
    ("pane_state", "expected"),
    [
        ("blocked", "blocked"),
        ("offline", "offline"),
        ("busy", "busy"),
        ("idle", "idle"),
    ],
)
def test_basic_status_mapping(classify_status, pane_state, expected):
    result = classify_status(pane_state=pane_state)

    assert _status(result) == expected, result


def test_done_but_busy_is_conflict_not_idle(classify_status):
    result = classify_status(task_state="done", pane_state="busy")

    assert _status(result) != "idle", result
    assert _status(result) in {"busy", "conflict", "unknown"}, result
    assert result.get("conflict") is True or "conflict" in _reason(result).lower()


def test_pane_idle_does_not_override_running_task(classify_status):
    result = classify_status(task_state="running", pane_state="idle")

    assert _status(result) != "idle", result
    assert _status(result) == "busy", result


def test_pane_idle_does_not_override_blocked_task(classify_status):
    result = classify_status(task_state="blocked", pane_state="idle")

    assert _status(result) == "blocked", result


def test_pane_idle_does_not_override_running_status(classify_status):
    result = classify_status(status="running", pane_state="idle")

    assert _status(result) != "idle", result
    assert _status(result) == "busy", result


def test_strong_completion_gets_three_minute_grace(completion_signal):
    within_grace = completion_signal(completed_at_s=NOW - 179)
    after_grace = completion_signal(completed_at_s=NOW - 181)

    assert not _should_remind(within_grace), within_grace
    assert _should_remind(after_grace), after_grace


def test_weak_completion_gets_eight_minute_grace(completion_signal):
    within_grace = completion_signal(
        completion_signal="weak",
        completed_at_s=NOW - 479,
    )
    after_grace = completion_signal(
        completion_signal="weak",
        completed_at_s=NOW - 481,
    )

    assert not _should_remind(within_grace), within_grace
    assert _should_remind(after_grace), after_grace


def test_unreported_completion_reminder_dedupes_for_thirty_minutes(completion_signal):
    within_dedupe = completion_signal(
        completed_at_s=NOW - 600,
        last_alert_at_s=NOW - 1_799,
    )
    after_dedupe = completion_signal(
        completed_at_s=NOW - 600,
        last_alert_at_s=NOW - 1_801,
    )

    assert not _should_remind(within_dedupe), within_dedupe
    assert _should_remind(after_dedupe), after_dedupe


def test_manager_report_resolves_unreported_completion(completion_signal):
    result = completion_signal(
        completed_at_s=NOW - 600,
        manager_reported_at_s=NOW - 60,
    )

    assert _resolved(result), result
    assert not _should_remind(result), result


def test_legacy_completion_api_uses_same_grace_and_dedupe():
    fn = _find_function(agent_status_signals, ("detect_completion_without_report",))
    dedupe_state = {}
    base = {
        "agent": "coder",
        "task_id": "T-P0-2-LEGACY",
        "completion_signal": "strong",
        "completed_at_s": NOW - 179,
    }

    within_grace = _to_mapping(_call_flex(fn, base, now=NOW))
    first_due = _to_mapping(
        _call_flex(fn, {**base, "completed_at_s": NOW - 181}, now=NOW)
    )
    first_deduped = _to_mapping(
        fn({**base, "completed_at_s": NOW - 600}, now=NOW, dedupe_state=dedupe_state)
    )
    within_dedupe = _to_mapping(
        fn({**base, "completed_at_s": NOW - 700}, now=NOW + 1_799, dedupe_state=dedupe_state)
    )
    after_dedupe = _to_mapping(
        fn({**base, "completed_at_s": NOW - 800}, now=NOW + 1_801, dedupe_state=dedupe_state)
    )
    weak_within_grace = _to_mapping(
        _call_flex(
            fn,
            {**base, "task_id": "T-P0-2-WEAK", "completion_signal": "weak", "completed_at_s": NOW - 479},
            now=NOW,
        )
    )
    weak_due = _to_mapping(
        _call_flex(
            fn,
            {**base, "task_id": "T-P0-2-WEAK", "completion_signal": "weak", "completed_at_s": NOW - 481},
            now=NOW,
        )
    )

    assert not _should_remind(within_grace), within_grace
    assert _should_remind(first_due), first_due
    assert _should_remind(first_deduped), first_deduped
    assert not _should_remind(within_dedupe), within_dedupe
    assert _should_remind(after_dedupe), after_dedupe
    assert not _should_remind(weak_within_grace), weak_within_grace
    assert _should_remind(weak_due), weak_due
