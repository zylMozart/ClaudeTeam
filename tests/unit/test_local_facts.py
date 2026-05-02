"""Tests for the local-facts store (inbox / status / log).

Each test runs inside `isolated_env()` so the state dir is fresh per test.
"""
from __future__ import annotations

from claudeteam.store import local_facts
from helpers import isolated_env


def test_append_then_list_messages():
    with isolated_env():
        mid = local_facts.append_message("worker", "manager", "hello", priority="高")
        rows = local_facts.list_messages("worker")
        assert len(rows) == 1
        assert rows[0]["local_id"] == mid
        assert rows[0]["content"] == "hello"
        assert rows[0]["priority"] == "高"
        assert rows[0]["read"] is False


def test_list_filters_by_agent_and_unread_only():
    with isolated_env():
        local_facts.append_message("a", "manager", "to a")
        local_facts.append_message("b", "manager", "to b")
        mid_unread = local_facts.append_message("a", "manager", "still unread")
        # mark first message read; second remains unread
        first_a = local_facts.list_messages("a")[0]
        local_facts.mark_read(first_a["local_id"])

        unread_a = local_facts.list_messages("a", unread_only=True)
        assert len(unread_a) == 1
        assert unread_a[0]["local_id"] == mid_unread

        all_b = local_facts.list_messages("b")
        assert len(all_b) == 1
        assert all_b[0]["content"] == "to b"


def test_mark_read_sets_flag_and_returns_false_on_miss():
    with isolated_env():
        mid = local_facts.append_message("a", "b", "x")
        assert local_facts.mark_read(mid) is True
        assert local_facts.list_messages("a", unread_only=True) == []
        assert local_facts.mark_read(mid) is True  # idempotent
        assert local_facts.mark_read("local_does_not_exist") is False


def test_status_upsert_then_get():
    with isolated_env():
        assert local_facts.get_status("a") is None
        local_facts.upsert_status("a", "进行中", "do thing")
        snap = local_facts.get_status("a")
        assert snap is not None
        assert snap["status"] == "进行中"
        assert snap["task"] == "do thing"
        assert snap["blocker"] == ""

        # update overwrites
        local_facts.upsert_status("a", "已完成", "done", blocker="")
        snap = local_facts.get_status("a")
        assert snap["status"] == "已完成"


def test_log_append_then_list():
    with isolated_env():
        local_facts.append_log("a", "info", "first")
        local_facts.append_log("a", "info", "second", ref="REF-1")
        local_facts.append_log("b", "info", "other agent")
        rows = local_facts.list_logs("a")
        assert len(rows) == 2
        assert rows[0]["content"] == "first"
        assert rows[1]["content"] == "second"
        assert rows[1]["ref"] == "REF-1"


def test_log_returns_empty_when_no_log_file():
    with isolated_env():
        # never appended → no log file
        assert local_facts.list_logs("a") == []


def test_facts_dir_uses_state_dir_env():
    with isolated_env() as tmp:
        facts_dir = tmp / "state" / "facts"
        local_facts.append_message("a", "b", "x")
        assert facts_dir.exists()
        assert (facts_dir / "inbox.json").exists()
