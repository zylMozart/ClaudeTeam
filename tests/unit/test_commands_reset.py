"""Tests for `claudeteam reset` — wipe runtime state."""
from __future__ import annotations

from helpers import isolated_env, run_cli, tmux_patch
from claudeteam.runtime import config, paths
from claudeteam.store import local_facts


def _fake_tmux_no_session():
    return tmux_patch(has_session=lambda s: False)


# ── happy path ──────────────────────────────────────────────────


def test_reset_with_yes_wipes_state_dir():
    team = {"agents": {"manager": {}}}
    with isolated_env(team=team), _fake_tmux_no_session():
        # produce some state
        local_facts.append_message("a", "b", "x")
        local_facts.touch_heartbeat("a")
        sd = paths.state_dir()
        assert sd.exists()

        rc, out, _ = run_cli(["reset", "--yes"])
        assert rc == 0
        assert "wiped" in out
        assert "reset complete" in out
        assert not sd.exists()


def test_reset_preserves_config_files():
    team = {"agents": {"manager": {}}}
    rc_cfg = {"chat_id": "oc_x"}
    with isolated_env(team=team, runtime_config=rc_cfg), _fake_tmux_no_session():
        team_path = config.team_file()
        rt_path = config.runtime_config_file()
        assert team_path.exists() and rt_path.exists()

        run_cli(["reset", "--yes"])
        assert team_path.exists() and rt_path.exists()


def test_reset_when_state_dir_does_not_exist_still_returns_zero():
    team = {"agents": {"manager": {}}}
    with isolated_env(team=team), _fake_tmux_no_session():
        sd = paths.state_dir()
        assert not sd.exists()  # never created
        rc, out, _ = run_cli(["reset", "--yes"])
        assert rc == 0
        assert "did not exist" in out


# ── safety ──────────────────────────────────────────────────────


def test_reset_without_yes_in_non_tty_aborts():
    team = {"agents": {"manager": {}}}
    with isolated_env(team=team), _fake_tmux_no_session():
        local_facts.append_message("a", "b", "x")
        sd = paths.state_dir()

        # run_cli redirects stdout/stderr but stdin remains the test runner's
        # — typically not a TTY. Reset should refuse.
        rc, _, err = run_cli(["reset"])
        assert rc == 1
        assert "aborted" in err
        assert sd.exists()  # state preserved


def test_reset_unexpected_args_returns_one():
    with isolated_env():
        rc, _, err = run_cli(["reset", "--bogus"])
        assert rc == 1
        assert "unexpected args" in err


def test_reset_help():
    rc, out, _ = run_cli(["reset", "--help"])
    assert rc == 0
    assert "usage: claudeteam reset" in out
