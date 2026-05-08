"""Tests for `claudeteam team` and `claudeteam workspace` (read-side)."""
from __future__ import annotations

import json

from helpers import isolated_env, run_cli
from claudeteam.store import local_facts


# ── team ──────────────────────────────────────────────────────────


def test_team_empty_when_no_agents():
    with isolated_env():
        rc, out, _ = run_cli(["team"])
        assert rc == 0
        assert "no agents have reported status yet" in out


def test_team_lists_all_agents_sorted_by_name():
    with isolated_env():
        run_cli(["status", "worker_b", "进行中", "doing b"])
        run_cli(["status", "worker_a", "已完成", "done a"])
        run_cli(["status", "worker_c", "阻塞", "stuck", "no api key"])
        rc, out, _ = run_cli(["team"])
        assert rc == 0
        lines = [l for l in out.splitlines() if l.strip()]
        assert len(lines) == 3
        # alphabetical
        assert lines[0].startswith("worker_a")
        assert lines[1].startswith("worker_b")
        assert lines[2].startswith("worker_c")
        # blocker shown for worker_c
        assert "⛔ no api key" in lines[2]


def test_team_appends_heartbeat_marker_when_recorded():
    with isolated_env():
        run_cli(["status", "agent", "进行中", "task"])
        # status command auto-touches heartbeat
        rc, out, _ = run_cli(["team"])
        assert rc == 0
        assert "♥" in out


def test_team_shows_relative_age():
    with isolated_env():
        run_cli(["status", "agent", "进行中", "task"])
        rc, out, _ = run_cli(["team"])
        assert rc == 0
        # latest write is < 1m ago
        assert "ago)" in out


def test_team_json_dumps_machine_readable_records():
    """`--json` emits a list[dict] consumable by CI / smoke conductors
    / peer agents — no emoji, no relative timestamps to parse."""
    with isolated_env():
        run_cli(["status", "worker_a", "进行中", "task A"])
        run_cli(["status", "worker_b", "已完成", "task B", "blocked on review"])
        rc, out, _ = run_cli(["team", "--json"])
        assert rc == 0
        data = json.loads(out)
        assert isinstance(data, list)
        assert len(data) == 2
        # Sorted alphabetically: worker_a before worker_b
        assert data[0]["agent"] == "worker_a"
        assert data[0]["status"] == "进行中"
        assert data[0]["task"] == "task A"
        assert data[0]["heartbeat_ms"] > 0  # status command touched it
        assert data[1]["agent"] == "worker_b"
        assert data[1]["blocker"] == "blocked on review"


def test_team_json_empty_returns_empty_list_not_message():
    """When no agents have reported, JSON mode emits `[]`, not the
    "no agents" friendly text — consumers can iterate without
    string-checking."""
    with isolated_env():
        rc, out, _ = run_cli(["team", "--json"])
        assert rc == 0
        assert json.loads(out) == []


# ── workspace ─────────────────────────────────────────────────────


def test_workspace_empty_returns_zero_with_message():
    with isolated_env():
        rc, out, _ = run_cli(["workspace", "nobody"])
        assert rc == 0
        assert "nobody: no log entries" in out


def test_workspace_lists_recent_log_entries():
    with isolated_env():
        run_cli(["log", "a", "info", "first"])
        run_cli(["log", "a", "task", "second", "TASK-1"])
        run_cli(["log", "b", "info", "should not appear"])
        rc, out, _ = run_cli(["workspace", "a"])
        assert rc == 0
        assert "a: last 2 log entries" in out
        assert "first" in out and "second" in out
        assert "(TASK-1)" in out
        assert "should not appear" not in out


def test_workspace_limit_caps_returned_rows():
    with isolated_env():
        for i in range(5):
            run_cli(["log", "a", "info", f"entry-{i}"])
        rc, out, _ = run_cli(["workspace", "a", "--limit", "2"])
        assert rc == 0
        assert "last 2 log entries" in out
        assert "entry-3" in out and "entry-4" in out
        assert "entry-0" not in out


def test_workspace_invalid_limit_returns_one():
    with isolated_env():
        rc, _, err = run_cli(["workspace", "a", "--limit", "abc"])
        assert rc == 1
        assert "usage:" in err


def test_workspace_zero_args_returns_one():
    rc, _, err = run_cli(["workspace"])
    assert rc == 1
    assert "usage:" in err


# ── store helper ───────────────────────────────────────────────────


def test_list_all_statuses_returns_sorted_rows():
    with isolated_env():
        local_facts.upsert_status("z", "进行中", "z task")
        local_facts.upsert_status("a", "已完成", "a task")
        rows = local_facts.list_all_statuses()
        assert [r["agent"] for r in rows] == ["a", "z"]


def test_list_all_statuses_empty_when_no_writes():
    with isolated_env():
        assert local_facts.list_all_statuses() == []
