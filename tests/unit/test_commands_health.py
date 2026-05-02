"""Tests for `claudeteam health`."""
from __future__ import annotations

import contextlib

from helpers import isolated_env, run_cli
from claudeteam.runtime import tmux


@contextlib.contextmanager
def _stub_tmux(*, session_alive: bool, panes_with_cli: list[str] = (),
               panes_without_cli: list[str] = ()):
    """Replace tmux.has_session/has_window/capture_pane for health probing."""
    panes_with_cli = list(panes_with_cli)
    panes_without_cli = list(panes_without_cli)
    all_panes = panes_with_cli + panes_without_cli

    def has_session(s):
        return session_alive

    def has_window(target):
        return str(target).split(":")[1] in all_panes

    def capture_pane(target, lines=80):
        agent = str(target).split(":")[1]
        if agent in panes_with_cli:
            return "bypass permissions on\n? for shortcuts\n>"
        return "$ "

    saved = (tmux.has_session, tmux.has_window, tmux.capture_pane)
    tmux.has_session, tmux.has_window, tmux.capture_pane = has_session, has_window, capture_pane
    try:
        yield
    finally:
        tmux.has_session, tmux.has_window, tmux.capture_pane = saved


# ── happy path ──────────────────────────────────────────────────


def test_health_all_green_returns_zero():
    team = {"session": "S", "agents": {"manager": {"cli": "claude-code"}}}
    rc_cfg = {"chat_id": "oc_x", "lark_profile": "prod"}
    with isolated_env(team=team, runtime_config=rc_cfg), _stub_tmux(
            session_alive=True, panes_with_cli=["manager"]):
        rc, out, _ = run_cli(["health"])
        assert rc == 0
        assert "✅ all green" in out
        assert "team.json" in out
        assert "chat_id: oc_x" in out
        assert "lark_profile: prod" in out
        assert "tmux session: S" in out
        assert "manager: pane ready" in out


# ── red checks ──────────────────────────────────────────────────


def test_health_returns_one_when_session_down():
    team = {"session": "S", "agents": {"manager": {}}}
    with isolated_env(team=team, runtime_config={"chat_id": "oc_x"}), _stub_tmux(
            session_alive=False):
        rc, out, _ = run_cli(["health"])
        assert rc == 1
        assert "tmux session S not running" in out


def test_health_returns_one_when_chat_id_blank():
    team = {"session": "S", "agents": {"manager": {}}}
    with isolated_env(team=team, runtime_config={"chat_id": ""}), _stub_tmux(
            session_alive=True, panes_with_cli=["manager"]):
        rc, out, _ = run_cli(["health"])
        assert rc == 1
        assert "empty chat_id" in out


def test_health_returns_one_when_team_json_missing():
    with isolated_env(runtime_config={"chat_id": "oc_x"}), _stub_tmux(
            session_alive=True):
        # don't call isolated_env(team=...) so file doesn't exist
        rc, out, _ = run_cli(["health"])
        assert rc == 1
        assert "team.json missing" in out


def test_health_returns_one_when_pane_window_missing():
    team = {"session": "S", "agents": {"manager": {}, "missing_w": {}}}
    with isolated_env(team=team, runtime_config={"chat_id": "oc_x"}), _stub_tmux(
            session_alive=True, panes_with_cli=["manager"]):
        rc, out, _ = run_cli(["health"])
        assert rc == 1
        assert "missing_w: no tmux window" in out


# ── warnings (non-fatal) ────────────────────────────────────────


def test_health_warns_when_pane_up_but_no_cli_marker():
    team = {"session": "S", "agents": {"manager": {}}}
    with isolated_env(team=team, runtime_config={"chat_id": "oc_x"}), _stub_tmux(
            session_alive=True, panes_with_cli=[], panes_without_cli=["manager"]):
        rc, out, _ = run_cli(["health"])
        assert rc == 0  # warning only
        assert "no CLI ready marker" in out


def test_health_warns_when_lark_profile_blank():
    team = {"session": "S", "agents": {"manager": {}}}
    with isolated_env(team=team, runtime_config={"chat_id": "oc_x", "lark_profile": ""}), _stub_tmux(
            session_alive=True, panes_with_cli=["manager"]):
        rc, out, _ = run_cli(["health"])
        assert rc == 0
        assert "lark_profile blank" in out


def test_health_warns_when_router_pid_missing():
    team = {"session": "S", "agents": {"manager": {}}}
    with isolated_env(team=team, runtime_config={"chat_id": "oc_x"}), _stub_tmux(
            session_alive=True, panes_with_cli=["manager"]):
        rc, out, _ = run_cli(["health"])
        assert rc == 0
        assert "router: no pid file" in out


def test_health_warns_when_cursor_empty():
    team = {"session": "S", "agents": {"manager": {}}}
    with isolated_env(team=team, runtime_config={"chat_id": "oc_x"}), _stub_tmux(
            session_alive=True, panes_with_cli=["manager"]):
        rc, out, _ = run_cli(["health"])
        assert rc == 0
        assert "router cursor: empty" in out


# ── help ────────────────────────────────────────────────────────


def test_health_help():
    rc, out, _ = run_cli(["health", "--help"])
    assert rc == 0
    assert "usage: claudeteam health" in out
