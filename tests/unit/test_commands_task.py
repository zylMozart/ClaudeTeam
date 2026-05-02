"""Tests for `claudeteam task` subcommand dispatcher."""
from __future__ import annotations

import io

from helpers import isolated_env, run_cli
from claudeteam import cli
from claudeteam.store import tasks


# ── create ────────────────────────────────────────────────────────


def test_task_create_minimal():
    with isolated_env():
        rc, out, _ = run_cli(["task", "create", "worker", "do task X"])
        assert rc == 0
        assert "created T-1" in out
        rows = tasks.list_tasks()
        assert rows[0]["title"] == "do task X"
        assert rows[0]["assignee"] == "worker"


def test_task_create_with_by_and_desc():
    with isolated_env():
        run_cli(["task", "create", "worker", "task name",
              "--by", "manager", "--desc", "root cause Y"])
        t = tasks.list_tasks()[0]
        assert t["creator"] == "manager"
        assert t["description"] == "root cause Y"


def test_task_create_title_with_spaces():
    with isolated_env():
        run_cli(["task", "create", "worker", "fix", "the", "broken", "thing"])
        t = tasks.list_tasks()[0]
        assert t["title"] == "fix the broken thing"


def test_task_create_missing_args_returns_one():
    with isolated_env():
        rc, _, err = run_cli(["task", "create", "worker"])
        assert rc == 1
        assert "usage:" in err


# ── update ────────────────────────────────────────────────────────


def test_task_update_status():
    with isolated_env():
        tasks.create("w", "x")
        rc, out, _ = run_cli(["task", "update", "T-1", "--status", "进行中"])
        assert rc == 0
        assert tasks.get("T-1")["status"] == "进行中"


def test_task_update_invalid_status_returns_one():
    with isolated_env():
        tasks.create("w", "x")
        rc, _, err = run_cli(["task", "update", "T-1", "--status", "bogus"])
        assert rc == 1
        assert "invalid status" in err


def test_task_update_unknown_id_returns_one():
    with isolated_env():
        rc, _, err = run_cli(["task", "update", "T-99", "--status", "已完成"])
        assert rc == 1
        assert "no such task" in err


def test_task_update_can_reassign_and_retitle():
    with isolated_env():
        tasks.create("w1", "old")
        run_cli(["task", "update", "T-1", "--assignee", "w2", "--title", "new"])
        t = tasks.get("T-1")
        assert t["assignee"] == "w2"
        assert t["title"] == "new"


# ── done shortcut ────────────────────────────────────────────────


def test_task_done_marks_completed():
    with isolated_env():
        tasks.create("w", "x")
        rc, out, _ = run_cli(["task", "done", "T-1"])
        assert rc == 0
        t = tasks.get("T-1")
        assert t["status"] == "已完成"
        assert t["completed_at"] is not None


# ── list / get ────────────────────────────────────────────────────


def test_task_list_empty():
    with isolated_env():
        rc, out, _ = run_cli(["task", "list"])
        assert rc == 0
        assert "no matching tasks" in out


def test_task_list_shows_count_and_each_row():
    with isolated_env():
        tasks.create("w", "first task")
        tasks.create("w", "second task")
        rc, out, _ = run_cli(["task", "list"])
        assert rc == 0
        assert "2 tasks" in out
        assert "first task" in out and "second task" in out


def test_task_list_filter_by_status_and_assignee():
    with isolated_env():
        tasks.create("alice", "a-task")
        tasks.create("bob", "b-task")
        tasks.create("alice", "a-done")
        tasks.update("T-3", status="已完成")

        rc, out, _ = run_cli(["task", "list", "--assignee", "alice"])
        assert rc == 0
        assert "a-task" in out and "a-done" in out
        assert "b-task" not in out

        rc, out, _ = run_cli(["task", "list", "--status", "已完成"])
        assert rc == 0
        assert "a-done" in out
        assert "a-task" not in out


def test_task_get_existing_renders_full_card():
    with isolated_env():
        tasks.create("w", "task one", description="d")
        rc, out, _ = run_cli(["task", "get", "T-1"])
        assert rc == 0
        assert "T-1" in out and "task one" in out
        assert "desc: d" in out


def test_task_get_unknown_id_returns_one():
    with isolated_env():
        rc, _, err = run_cli(["task", "get", "T-99"])
        assert rc == 1
        assert "no such task" in err


# ── dispatcher ───────────────────────────────────────────────────


def test_task_no_args_prints_usage():
    rc, out, _ = run_cli(["task"])
    # treated as "show usage"; behaviour-wise rc==1 since no subcmd
    assert "usage:" in out
    assert rc == 1


def test_task_unknown_subcommand_returns_one():
    rc, _, err = run_cli(["task", "invent"])
    assert rc == 1
    assert "unknown task subcommand" in err
