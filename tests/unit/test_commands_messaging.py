"""Tests for `claudeteam send / inbox / read` commands.

Goes through cli.main([...]) so we exercise the dispatch + handler
contract end-to-end (without spawning a subprocess).
"""
from __future__ import annotations

import io

from helpers import isolated_env, run_cli
from claudeteam import cli
from claudeteam.store import local_facts


def test_send_writes_inbox_and_prints_local_id():
    with isolated_env():
        rc, out, err = run_cli(["send", "worker", "manager", "do task X"])
        assert rc == 0, err
        assert "sent → worker" in out
        assert "local_id=msg_" in out

        rows = local_facts.list_messages("worker")
        assert len(rows) == 1
        assert rows[0]["content"] == "do task X"
        assert rows[0]["from"] == "manager"


def test_send_priority_param_threads_through():
    with isolated_env():
        run_cli(["send", "a", "b", "msg", "高"])
        rows = local_facts.list_messages("a")
        assert rows[0]["priority"] == "高"


def test_send_missing_args_returns_one_with_usage_to_stderr():
    rc, out, err = run_cli(["send", "only-one-arg"])
    assert rc == 1
    assert "usage: claudeteam send" in err


def test_inbox_lists_unread_with_local_id_and_returns_zero():
    with isolated_env():
        run_cli(["send", "w", "m", "first"])
        run_cli(["send", "w", "m", "second"])
        rc, out, _ = run_cli(["inbox", "w"])
        assert rc == 0
        assert "📬 w: 2 unread" in out
        assert "first" in out and "second" in out


def test_inbox_empty_prints_no_unread():
    with isolated_env():
        rc, out, _ = run_cli(["inbox", "nobody"])
        assert rc == 0
        assert "📭 nobody: no unread messages" in out


def test_read_marks_then_inbox_drops_it():
    with isolated_env():
        run_cli(["send", "w", "m", "task A"])
        msgs = local_facts.list_messages("w")
        local_id = msgs[0]["local_id"]

        rc, out, _ = run_cli(["read", local_id])
        assert rc == 0
        assert "marked read" in out

        rc, out, _ = run_cli(["inbox", "w"])
        assert rc == 0
        assert "📭 w: no unread messages" in out


def test_read_unknown_id_returns_one():
    with isolated_env():
        rc, _, err = run_cli(["read", "msg_does_not_exist"])
        assert rc == 1
        assert "no such message" in err
