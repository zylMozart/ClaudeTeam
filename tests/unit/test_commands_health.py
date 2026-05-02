"""Tests for `claudeteam health`."""
from __future__ import annotations

import shutil

from helpers import attr_patch, env_patch, isolated_env, run_cli, tmux_patch


def _stub_tmux(*, session_alive: bool, panes_with_cli: list[str] = (),
               panes_without_cli: list[str] = ()):
    """Replace tmux.has_session/has_window/capture_pane for health probing."""
    all_panes = list(panes_with_cli) + list(panes_without_cli)

    def capture_pane(target, lines=80):
        if target.window in panes_with_cli:
            return "bypass permissions on\n? for shortcuts\n>"
        return "$ "

    return tmux_patch(
        has_session=lambda s: session_alive,
        has_window=lambda target: target.window in all_panes,
        capture_pane=capture_pane,
    )


# ── happy path ──────────────────────────────────────────────────


def test_health_all_green_returns_zero():
    """No reds AND no warnings → green footer."""
    team = {"session": "S", "agents": {"manager": {"cli": "claude-code"}}}
    rc_cfg = {"chat_id": "oc_x", "lark_profile": "prod"}
    with isolated_env(team=team, runtime_config=rc_cfg), _stub_tmux(
            session_alive=True, panes_with_cli=["manager"]), \
            env_patch(HTTPS_PROXY=None, HTTP_PROXY=None):
        rc, out, _ = run_cli(["health"])
        assert rc == 0
        assert "team.json" in out
        assert "chat_id: oc_x" in out
        assert "lark_profile: prod" in out
        assert "tmux session: S" in out
        assert "manager: pane ready" in out
        # Daemons / cursor lines are ⚠️ / ℹ️ in this isolated test rig
        # (no pid files); footer should report warnings, not "all green"
        assert "no errors" in out
        assert "warning" in out


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
        assert "CLI not ready yet" in out


def test_health_lazy_pane_without_marker_is_green():
    """A pane marked lazy in team.json is expected to have no ready marker
    until first message. Don't yellow-flag the operator over expected state."""
    team = {"session": "S", "agents": {"sleeper": {"cli": "claude-code", "lazy": True}}}
    with isolated_env(team=team, runtime_config={"chat_id": "oc_x"}), _stub_tmux(
            session_alive=True, panes_with_cli=[], panes_without_cli=["sleeper"]):
        rc, out, _ = run_cli(["health"])
        assert rc == 0
        assert "lazy pane" in out
        assert "CLI not ready yet" not in out


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


def test_health_info_when_cursor_empty():
    """Empty cursor on first run is informational, not a warning — it only
    advances on inbound events, not self-originated say calls."""
    team = {"session": "S", "agents": {"manager": {}}}
    with isolated_env(team=team, runtime_config={"chat_id": "oc_x"}), _stub_tmux(
            session_alive=True, panes_with_cli=["manager"]):
        rc, out, _ = run_cli(["health"])
        assert rc == 0
        assert "router cursor: empty" in out
        assert "ℹ️" in out  # info marker, not warn marker
        # ensure "advances on first inbound event" is in the cursor line
        assert "first inbound event" in out


# ── binaries / env ──────────────────────────────────────────────


def _stub_which(present: set[str]):
    """shutil.which replacement: returns a fake path for names in `present`,
    None for everything else. Doesn't fall through to the real PATH."""
    return attr_patch(
        shutil,
        which=lambda name, *a, **kw: f"/usr/bin/{name}" if name in present else None,
    )


def test_health_red_when_binary_missing():
    team = {"session": "S", "agents": {"m": {"cli": "claude-code"}}}
    with isolated_env(team=team, runtime_config={"chat_id": "oc_x"}), _stub_tmux(
            session_alive=True, panes_with_cli=["m"]), _stub_which(set()):
        rc, out, _ = run_cli(["health"])
        assert rc == 1
        assert "claude: not on PATH" in out


def test_health_green_when_binaries_present():
    team = {"session": "S", "agents": {"m": {"cli": "claude-code"}}}
    with isolated_env(team=team, runtime_config={"chat_id": "oc_x"}), _stub_tmux(
            session_alive=True, panes_with_cli=["m"]), _stub_which({"claude"}):
        rc, out, _ = run_cli(["health"])
        assert "claude: /usr/bin/claude" in out


def test_health_warns_when_proxy_set_without_no_proxy():
    team = {"session": "S", "agents": {"m": {}}}
    with isolated_env(team=team, runtime_config={"chat_id": "oc_x"}), _stub_tmux(
            session_alive=True, panes_with_cli=["m"]), \
            env_patch(HTTPS_PROXY="http://proxy:7890", LARK_CLI_NO_PROXY=None):
        rc, out, _ = run_cli(["health"])
        assert "HTTPS_PROXY=http://proxy:7890 set without LARK_CLI_NO_PROXY" in out


def test_health_silent_when_proxy_unset():
    team = {"session": "S", "agents": {"m": {}}}
    with isolated_env(team=team, runtime_config={"chat_id": "oc_x"}), _stub_tmux(
            session_alive=True, panes_with_cli=["m"]), \
            env_patch(HTTPS_PROXY=None, HTTP_PROXY=None):
        rc, out, _ = run_cli(["health"])
        assert "HTTPS_PROXY" not in out


# ── help ────────────────────────────────────────────────────────


def test_health_help():
    rc, out, _ = run_cli(["health", "--help"])
    assert rc == 0
    assert "usage: claudeteam health" in out
