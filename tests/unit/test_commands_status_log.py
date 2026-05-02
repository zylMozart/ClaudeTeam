"""Tests for `claudeteam status` (set+show) and `claudeteam log` (append)."""
from __future__ import annotations

import io

from helpers import isolated_env, run_cli
from claudeteam import cli
from claudeteam.store import local_facts


# ── status ─────────────────────────────────────────────────────────


def test_status_set_writes_store_and_prints_summary():
    with isolated_env():
        rc, out, _ = run_cli(["status", "worker", "进行中", "do task X"])
        assert rc == 0
        assert "worker → 进行中: do task X" in out
        snap = local_facts.get_status("worker")
        assert snap is not None
        assert snap["status"] == "进行中"
        assert snap["task"] == "do task X"
        assert snap["blocker"] == ""


def test_status_set_with_blocker_appends_marker():
    with isolated_env():
        rc, out, _ = run_cli(["status", "worker", "阻塞", "stuck", "missing API key"])
        assert rc == 0
        assert "⛔ missing API key" in out
        snap = local_facts.get_status("worker")
        assert snap["blocker"] == "missing API key"


def test_status_show_when_unrecorded():
    with isolated_env():
        rc, out, _ = run_cli(["status", "noone"])
        assert rc == 0
        assert "noone: no status recorded" in out


def test_status_show_after_set():
    with isolated_env():
        run_cli(["status", "a", "进行中", "task"])
        rc, out, _ = run_cli(["status", "a"])
        assert rc == 0
        assert "a: 进行中 | task" in out


def test_status_set_idempotent_overwrites_previous():
    with isolated_env():
        run_cli(["status", "a", "进行中", "first"])
        run_cli(["status", "a", "已完成", "second"])
        snap = local_facts.get_status("a")
        assert snap["status"] == "已完成"
        assert snap["task"] == "second"


def test_status_zero_args_returns_one_with_usage():
    rc, _, err = run_cli(["status"])
    assert rc == 1
    assert "usage:" in err


def test_status_set_missing_state_or_task_returns_one():
    with isolated_env():
        rc, _, err = run_cli(["status", "agent", "进行中"])
        assert rc == 1
        assert "usage:" in err


# ── log ────────────────────────────────────────────────────────────


def test_log_appends_to_jsonl_and_prints_id():
    with isolated_env():
        rc, out, _ = run_cli(["log", "worker", "info", "checkpoint reached"])
        assert rc == 0
        assert "logged: worker/info" in out
        rows = local_facts.list_logs("worker")
        assert len(rows) == 1
        assert rows[0]["content"] == "checkpoint reached"
        assert rows[0]["ref"] == ""


def test_log_with_ref():
    with isolated_env():
        run_cli(["log", "worker", "task", "did the thing", "TASK-7"])
        rows = local_facts.list_logs("worker")
        assert rows[0]["ref"] == "TASK-7"


def test_log_appends_multiple_in_order():
    with isolated_env():
        run_cli(["log", "a", "info", "first"])
        run_cli(["log", "a", "info", "second"])
        run_cli(["log", "b", "info", "other"])
        a_rows = local_facts.list_logs("a")
        b_rows = local_facts.list_logs("b")
        assert [r["content"] for r in a_rows] == ["first", "second"]
        assert len(b_rows) == 1


def test_log_missing_args_returns_one():
    rc, _, err = run_cli(["log", "agent", "info"])
    assert rc == 1
    assert "usage: claudeteam log" in err
