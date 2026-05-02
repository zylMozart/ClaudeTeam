"""Tests for `claudeteam up` and `claudeteam down` — composite lifecycle."""
from __future__ import annotations

import contextlib
import subprocess

from helpers import attr_patch, isolated_env, run_cli, tmux_patch
from claudeteam.commands import up as _up, down as _down
from claudeteam.runtime import paths


@contextlib.contextmanager
def _fake_tmux(session_alive=False):
    state = {"session_alive": session_alive, "session_killed": False, "calls": []}

    def has_session(s):
        state["calls"].append(("has_session", s))
        return state["session_alive"]

    def kill_session(s):
        state["calls"].append(("kill_session", s))
        state["session_alive"] = False
        state["session_killed"] = True
        return True

    with tmux_patch(has_session=has_session, kill_session=kill_session):
        yield state


@contextlib.contextmanager
def _fake_popen():
    """Replace subprocess.Popen used by up.py."""
    calls = []

    class _FakeProc:
        def __init__(self, argv):
            self.argv = argv

    def fake_popen(argv, *args, **kwargs):
        calls.append(list(argv))
        # Simulate the daemon writing its pid file
        if argv[:2] == ["claudeteam", "router"]:
            paths.ensure_state_dir()
            paths.router_pid_file().write_text("12345", encoding="utf-8")
        elif argv[:2] == ["claudeteam", "watchdog"]:
            paths.ensure_state_dir()
            paths.watchdog_pid_file().write_text("12346", encoding="utf-8")
        return _FakeProc(argv)

    with attr_patch(subprocess, Popen=fake_popen):
        yield calls


@contextlib.contextmanager
def _fake_alive(answers):
    """Make watchdog.is_alive return successive scripted booleans."""
    from claudeteam.commands import up as _up_mod
    from claudeteam.commands import down as _down_mod
    from claudeteam.runtime import watchdog as _wd
    from claudeteam.commands import health as _health_mod
    iterator = iter(answers)

    def fake(spec, **kwargs):
        try:
            return next(iterator)
        except StopIteration:
            return False

    saved = _up_mod.is_alive
    _up_mod.is_alive = fake
    try:
        yield
    finally:
        _up_mod.is_alive = saved


# ── up ──────────────────────────────────────────────────────────


def test_up_starts_session_and_spawns_two_daemons():
    team = {"session": "S",
            "agents": {"manager": {"cli": "claude-code"}}}
    extras = tmux_patch(
        new_session=lambda *a, **kw: True,
        new_window=lambda *a, **kw: True,
        spawn_agent=lambda *a, **kw: True,
    )
    with isolated_env(team=team), _fake_tmux(session_alive=False), \
            _fake_popen() as popen_calls, _fake_alive([False, False]), extras:
        rc, out, _ = run_cli(["up"])
        assert rc == 0
        assert "team up" in out
        assert ["claudeteam", "router"] in popen_calls
        assert ["claudeteam", "watchdog"] in popen_calls


def test_up_skips_session_when_already_running():
    team = {"session": "S", "agents": {"manager": {}}}
    with isolated_env(team=team), _fake_tmux(session_alive=True), \
            _fake_popen() as popen_calls, _fake_alive([False, False]):
        rc, out, _ = run_cli(["up"])
        assert rc == 0
        assert "already running, skipping start" in out
        assert ["claudeteam", "router"] in popen_calls


def test_up_skips_alive_daemons():
    team = {"session": "S", "agents": {"manager": {}}}
    with isolated_env(team=team), _fake_tmux(session_alive=True), \
            _fake_popen() as popen_calls, _fake_alive([True, True]):
        rc, out, _ = run_cli(["up"])
        assert rc == 0
        assert "router already alive" in out
        assert "watchdog already alive" in out
        assert popen_calls == []


def test_up_help():
    rc, out, _ = run_cli(["up", "--help"])
    assert rc == 0
    assert "usage: claudeteam up" in out


# ── down ────────────────────────────────────────────────────────


def test_down_skips_when_no_pid_files_and_no_session():
    team = {"session": "S", "agents": {"manager": {}}}
    with isolated_env(team=team), _fake_tmux(session_alive=False):
        rc, out, _ = run_cli(["down"])
        assert rc == 0
        assert "router: no pid file" in out
        assert "watchdog: no pid file" in out
        assert "tmux session S not running" in out


def test_down_kills_alive_pid_then_tmux():
    """When pid files point to a fake process, down should SIGTERM and clean up."""
    import os
    import signal

    team = {"session": "S", "agents": {"manager": {}}}
    with isolated_env(team=team) as tmp, _fake_tmux(session_alive=True) as tx:
        # Use *our* pid as the pid in the file — we know we're alive,
        # and we'll intercept os.kill so we don't actually die.
        my_pid = os.getpid()
        paths.ensure_state_dir()
        paths.router_pid_file().write_text(str(my_pid), encoding="utf-8")
        paths.watchdog_pid_file().write_text(str(my_pid), encoding="utf-8")

        kill_calls = []
        check_calls = []
        saved_kill = os.kill

        def fake_kill(pid, sig):
            kill_calls.append((pid, sig))
            if sig == 0:
                check_calls.append(pid)
                # After SIGTERM, simulate process exit on second probe
                if check_calls.count(pid) >= 2:
                    raise ProcessLookupError()
                return None
            # SIGTERM — pretend it was delivered
            return None

        os.kill = fake_kill
        try:
            rc, out, _ = run_cli(["down"])
        finally:
            os.kill = saved_kill
        assert rc == 0
        assert "router: pid" in out and "stopped" in out
        assert "watchdog: pid" in out
        assert tx["session_killed"]
        assert not paths.router_pid_file().exists()
        assert not paths.watchdog_pid_file().exists()


def test_down_handles_already_dead_pid():
    import os

    team = {"session": "S", "agents": {"manager": {}}}
    with isolated_env(team=team), _fake_tmux(session_alive=False):
        paths.ensure_state_dir()
        paths.router_pid_file().write_text("99999", encoding="utf-8")

        def fake_kill(pid, sig):
            raise ProcessLookupError()

        saved = os.kill
        os.kill = fake_kill
        try:
            rc, out, _ = run_cli(["down"])
        finally:
            os.kill = saved
        assert rc == 0
        assert "already dead" in out
        assert not paths.router_pid_file().exists()


def test_down_help():
    rc, out, _ = run_cli(["down", "--help"])
    assert rc == 0
    assert "usage: claudeteam down" in out
