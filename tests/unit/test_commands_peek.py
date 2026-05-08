"""Tests for `claudeteam peek <agent>` — local pane capture."""
from __future__ import annotations

from helpers import isolated_env, run_cli, tmux_patch


_TEAM = {"session": "S", "agents": {"manager": {}, "worker_cc": {}}}


def test_peek_captures_pane_buffer_to_stdout():
    """Default N=30 lines; output is the raw pane buffer rstripped."""
    captures = []

    def fake_capture(target, lines=80):
        captures.append({"target": str(target), "lines": lines})
        return "line 1\nline 2\nline 3\n"

    with isolated_env(team=_TEAM), tmux_patch(
            has_window=lambda t: True,
            capture_pane=fake_capture):
        rc, out, _ = run_cli(["peek", "manager"])
    assert rc == 0
    assert "line 1" in out and "line 2" in out and "line 3" in out
    # default N
    assert captures[0]["lines"] == 30
    assert captures[0]["target"] == "S:manager"


def test_peek_respects_explicit_n():
    captures = []

    def fake_capture(target, lines=80):
        captures.append(lines)
        return "x\n"

    with isolated_env(team=_TEAM), tmux_patch(
            has_window=lambda t: True,
            capture_pane=fake_capture):
        rc, _, _ = run_cli(["peek", "worker_cc", "100"])
    assert rc == 0
    assert captures == [100]


def test_peek_clamps_n_to_max():
    """N over 2000 must be clamped (the slash counterpart's _MAX_TMUX_LINES
    is the same — keeps both views consistent)."""
    captures = []

    def fake_capture(target, lines=80):
        captures.append(lines)
        return "x\n"

    with isolated_env(team=_TEAM), tmux_patch(
            has_window=lambda t: True,
            capture_pane=fake_capture):
        run_cli(["peek", "manager", "999999"])
    assert captures == [2000]


def test_peek_invalid_n_returns_error():
    with isolated_env(team=_TEAM):
        rc, _, err = run_cli(["peek", "manager", "abc"])
    assert rc == 1
    assert "must be a positive integer" in err


def test_peek_unknown_agent_returns_error():
    with isolated_env(team=_TEAM):
        rc, _, err = run_cli(["peek", "ghost"])
    assert rc == 1
    assert "unknown agent" in err


def test_peek_no_pane_returns_error():
    with isolated_env(team=_TEAM), tmux_patch(has_window=lambda t: False):
        rc, _, err = run_cli(["peek", "manager"])
    assert rc == 1
    assert "no pane" in err


def test_peek_empty_buffer_renders_friendly_placeholder():
    """When tmux returns nothing (newly-spawned pane, before any output),
    print a `(empty buffer for <agent>)` placeholder so the operator
    doesn't think peek silently failed."""
    with isolated_env(team=_TEAM), tmux_patch(
            has_window=lambda t: True,
            capture_pane=lambda t, lines=80: ""):
        rc, out, _ = run_cli(["peek", "manager"])
    assert rc == 0
    assert "(empty buffer for manager)" in out


def test_peek_zero_args_returns_usage():
    rc, _, err = run_cli(["peek"])
    assert rc == 1
    assert "usage:" in err


def test_peek_help():
    rc, out, _ = run_cli(["peek", "--help"])
    assert rc == 0
    assert "usage: claudeteam peek" in out


def test_peek_registered_in_cli():
    from claudeteam.cli import COMMANDS
    assert "peek" in COMMANDS
