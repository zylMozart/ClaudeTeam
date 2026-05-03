"""Tests for `claudeteam forget <agent>` — per-agent memory wipe."""
from __future__ import annotations

from helpers import isolated_env, run_cli
from claudeteam.store import memory


def test_forget_without_yes_refuses_and_returns_error():
    """Operator must opt in with --yes; otherwise we refuse and tell
    them to recall first. Round-96 added this guardrail; reset command
    is the whole-state nuke. forget is the scalpel."""
    with isolated_env():
        memory.append("manager", "note", "important")
        rc, _, err = run_cli(["forget", "manager"])
        assert rc == 1
        assert "without --yes" in err
        # Memory still there
        assert len(memory.list_recent("manager")) == 1


def test_forget_with_yes_wipes_and_reports_count():
    with isolated_env():
        memory.append("manager", "note", "a")
        memory.append("manager", "note", "b")
        memory.append("manager", "note", "c")
        rc, out, _ = run_cli(["forget", "manager", "--yes"])
        assert rc == 0
        assert "🗑" in out
        assert "3 memory entries" in out
        assert memory.list_recent("manager") == []


def test_forget_with_yes_singular_for_one_entry():
    """Pluralisation: 1 entry → 'entry' (singular), not 'entries'."""
    with isolated_env():
        memory.append("worker_cc", "note", "single")
        rc, out, _ = run_cli(["forget", "worker_cc", "--yes"])
        assert rc == 0
        assert "1 memory entry" in out


def test_forget_empty_memory_with_yes_is_a_no_op():
    """Wiping an already-empty memory: rc=0, friendly noop message."""
    with isolated_env():
        rc, out, _ = run_cli(["forget", "ghost", "--yes"])
        assert rc == 0
        assert "nothing to forget" in out


def test_forget_does_not_affect_other_agents():
    """Per-agent scalpel — wiping `manager` MUST not touch `worker_cc`."""
    with isolated_env():
        memory.append("manager", "note", "m")
        memory.append("worker_cc", "note", "w")
        run_cli(["forget", "manager", "--yes"])
        assert memory.list_recent("manager") == []
        assert len(memory.list_recent("worker_cc")) == 1


def test_forget_zero_args_returns_usage():
    rc, _, err = run_cli(["forget"])
    assert rc == 1
    assert "usage:" in err


def test_forget_help():
    rc, out, _ = run_cli(["forget", "--help"])
    assert rc == 0
    assert "usage: claudeteam forget" in out


def test_forget_registered_in_cli():
    from claudeteam.cli import COMMANDS
    assert "forget" in COMMANDS
