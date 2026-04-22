from __future__ import annotations

import pytest

from agent_status_evidence import (
    build_agent_evidence,
    collect_from_manager_inbox,
    collect_from_pending_queue,
    collect_from_status_table_row,
    collect_from_task_tracker,
    collect_from_tmux_snapshot,
    collect_from_workspace_logs,
)
from agent_status_signals import evaluate_completion_report_signal, normalize_agent_state


pytestmark = pytest.mark.unit

NOW = 1_800_000_000


def test_task_tracker_maps_running_and_done_tasks():
    running = collect_from_task_tracker(
        {
            "tasks": [
                {"task_id": "OLD", "assignee": "coder", "status": "已完成", "updated_at": NOW - 500},
                {"task_id": "RUN", "assignee": "coder", "status": "进行中", "updated_at": NOW - 100},
            ]
        },
        "coder",
    )
    done = collect_from_task_tracker(
        {
            "tasks": [
                {"task_id": "DONE", "assignee": "coder", "status": "已完成", "updated_at": NOW - 240},
            ]
        },
        "coder",
    )

    assert running["task_id"] == "RUN"
    assert running["task_state"] == "running"
    assert done["task_id"] == "DONE"
    assert done["task_state"] == "done"
    assert done["completed_at_s"] == NOW - 240
    assert done["completion_signal"] == "strong"


def test_status_table_maps_blocked_stale_and_source_error():
    blocked = collect_from_status_table_row(
        {
            "Agent名称": "coder",
            "状态": "阻塞",
            "阻塞原因": "waiting for review",
            "更新时间": NOW - 60,
            "now": NOW,
        },
        "coder",
    )
    stale = collect_from_status_table_row(
        {
            "Agent名称": "coder",
            "状态": "待命",
            "更新时间": NOW - 3600,
            "now": NOW,
        },
        "coder",
    )
    failed = collect_from_status_table_row(
        {"source_error": True, "source_error_detail": "bitable unavailable"},
        "coder",
    )

    assert blocked["status"] == "blocked"
    assert blocked["blocked"] is True
    assert blocked["blocked_reason"] == "waiting for review"
    assert stale["status_table_stale"] is True
    assert "status" not in stale
    assert normalize_agent_state(stale, NOW)["state"] == "unknown"
    assert failed["status_table_source_error"] is True
    assert failed["source_error"] is True
    assert normalize_agent_state(failed, NOW)["state"] == "unknown"


def test_pending_queue_counts_user_messages():
    evidence = collect_from_pending_queue(
        {
            "coder": [
                {"msg_id": "u1", "is_user_msg": True, "queued_at": NOW - 30},
                {"msg_id": "s1", "is_user_msg": False, "queued_at": NOW - 10},
            ]
        },
        "coder",
    )

    assert evidence["pending_total"] == 2
    assert evidence["pending_user_messages"] == 1
    assert evidence["oldest_pending_at"] == NOW - 30


def test_manager_inbox_detects_report_after_completion():
    evidence = collect_from_manager_inbox(
        [
            {
                "record_id": "before",
                "收件人": "manager",
                "发件人": "coder",
                "消息内容": "working",
                "时间": NOW - 400,
                "已读": False,
            },
            {
                "record_id": "after",
                "收件人": "manager",
                "发件人": "coder",
                "消息内容": "done",
                "时间": NOW - 100,
                "已读": True,
            },
            {
                "record_id": "other",
                "收件人": "manager",
                "发件人": "devops",
                "消息内容": "done",
                "时间": NOW - 50,
                "已读": False,
            },
        ],
        "coder",
        completed_at=NOW - 200,
    )

    assert evidence["already_reported"] is True
    assert evidence["manager_reported_at_s"] == NOW - 100
    assert evidence["manager_report_message_id"] == "after"
    assert evidence["manager_inbox_from_agent_count"] == 1
    assert evidence["manager_unread_count"] == 2


def test_workspace_logs_detect_strong_and_weak_completion():
    strong = collect_from_workspace_logs(
        [
            {
                "类型": "任务完成",
                "内容": "已完成并验证通过，交付路径 tests/unit/test_agent_status_evidence.py",
                "时间": NOW - 220,
                "关联对象": "T-STRONG",
            }
        ],
        "coder",
    )
    weak = collect_from_workspace_logs(
        [
            {
                "类型": "记录",
                "内容": "已修复，等待复核",
                "时间": NOW - 500,
                "关联对象": "",
            }
        ],
        "coder",
    )

    assert strong["completion_signal"] == "strong"
    assert strong["completion_log_hit"] is True
    assert strong["completed_at_s"] == NOW - 220
    assert strong["task_id"] == "T-STRONG"
    assert weak["completion_signal"] == "weak"
    assert weak["completion_log_hit"] is True


@pytest.mark.parametrize(
    ("snapshot", "expected"),
    [
        ({"pane_exists": True, "idle_marker": True, "pane_tail": ["ready"]}, "idle"),
        ({"pane_exists": True, "busy_marker": True, "pane_tail": ["running"]}, "busy"),
        ({"pane_exists": False, "pane_tail": None}, "offline"),
        ({"pane_exists": True, "permission_prompt": True, "pane_tail": ["approve?"]}, "blocked"),
    ],
)
def test_tmux_snapshot_maps_idle_busy_offline_blocked(snapshot, expected):
    evidence = collect_from_tmux_snapshot(snapshot, "coder")

    assert evidence["pane_state"] == expected
    assert normalize_agent_state(evidence, NOW)["state"] == expected


def test_build_agent_evidence_pending_user_message_blocks_idle():
    evidence = build_agent_evidence(
        "coder",
        {
            "tmux_snapshot": {"pane_exists": True, "idle_marker": True, "pane_tail": ["ready"]},
            "pending_queue": {"coder": [{"msg_id": "u1", "is_user_msg": True, "queued_at": NOW - 20}]},
        },
        now=NOW,
    )
    state = normalize_agent_state(evidence, NOW)

    assert evidence["agent"] == "coder"
    assert evidence["sources"] == ["tmux_pane", "pending_queue"]
    assert evidence["pending_user_messages"] == 1
    assert state["state"] == "busy"
    assert "pending_user_messages" in state["reason"]


def test_build_agent_evidence_completion_without_report_end_to_end():
    evidence = build_agent_evidence(
        "coder",
        {
            "workspace_logs": [
                {
                    "类型": "任务完成",
                    "内容": "已完成，验证通过，交付路径 scripts/agent_status_evidence.py",
                    "时间": NOW - 240,
                    "关联对象": "T-P0-3",
                }
            ],
            "manager_inbox": [],
        },
        now=NOW,
    )
    signal = evaluate_completion_report_signal(evidence, NOW)

    assert evidence["completion_signal"] == "strong"
    assert signal["should_remind"] is True
    assert signal["dedupe_key"] == "coder:T-P0-3"


def test_completion_no_alert_when_manager_inbox_source_error():
    evidence = build_agent_evidence(
        "coder",
        {
            "workspace_logs": [
                {
                    "类型": "任务完成",
                    "内容": "已完成，验证通过，交付路径 scripts/agent_status_evidence.py",
                    "时间": NOW - 240,
                    "关联对象": "T-P0-3",
                }
            ],
            "manager_inbox": {
                "source_error": True,
                "source_error_detail": "manager inbox unavailable",
            },
        },
        now=NOW,
    )
    signal = evaluate_completion_report_signal(evidence, NOW)

    assert evidence["manager_inbox_source_error"] is True
    assert evidence["source_error"] is True
    assert signal["state"] == "unknown"
    assert signal["source_error"] is True
    assert signal["should_remind"] is False
    assert signal["reason"] == "manager_inbox_source_error"


def test_task_done_strong_not_downgraded_by_empty_workspace_log():
    evidence = build_agent_evidence(
        "coder",
        {
            "task_tracker": {
                "tasks": [
                    {
                        "task_id": "T-DONE",
                        "assignee": "coder",
                        "status": "已完成",
                        "updated_at": NOW - 240,
                    }
                ]
            },
            "workspace_logs": [],
        },
        now=NOW,
    )
    signal = evaluate_completion_report_signal(evidence, NOW)

    assert evidence["task_id"] == "T-DONE"
    assert evidence["task_state"] == "done"
    assert evidence["completion_signal"] == "strong"
    assert evidence["completed_at_s"] == NOW - 240
    assert signal["grace_secs"] == 180
    assert signal["should_remind"] is True


def test_task_done_strong_not_downgraded_by_weak_workspace_log():
    evidence = build_agent_evidence(
        "coder",
        {
            "task_tracker": {
                "tasks": [
                    {
                        "task_id": "T-DONE",
                        "assignee": "coder",
                        "status": "已完成",
                        "updated_at": NOW - 240,
                    }
                ]
            },
            "workspace_logs": [
                {
                    "类型": "记录",
                    "内容": "已修复，等待复核",
                    "时间": NOW - 100,
                    "关联对象": "T-WEAK",
                }
            ],
        },
        now=NOW,
    )
    signal = evaluate_completion_report_signal(evidence, NOW)

    assert evidence["task_id"] == "T-DONE"
    assert evidence["completion_signal"] == "strong"
    assert evidence["completed_at_s"] == NOW - 240
    assert signal["grace_secs"] == 180
    assert signal["should_remind"] is True


def test_build_agent_evidence_manager_report_resolves_completion():
    evidence = build_agent_evidence(
        "coder",
        {
            "workspace_logs": [
                {
                    "类型": "记录",
                    "内容": "已修复，等待复核",
                    "时间": NOW - 600,
                    "关联对象": "T-WEAK",
                }
            ],
            "manager_inbox": [
                {
                    "record_id": "r1",
                    "收件人": "manager",
                    "发件人": "coder",
                    "消息内容": "T-WEAK 已修复",
                    "时间": NOW - 100,
                    "已读": True,
                }
            ],
        },
        now=NOW,
    )
    signal = evaluate_completion_report_signal(evidence, NOW)

    assert evidence["completion_signal"] == "weak"
    assert evidence["already_reported"] is True
    assert signal["state"] == "resolved"
    assert signal["should_remind"] is False
