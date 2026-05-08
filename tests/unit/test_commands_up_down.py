"""Tests for `claudeteam up` and `claudeteam down` — composite lifecycle."""
from __future__ import annotations

import contextlib
import os
import subprocess

from helpers import attr_patch, isolated_env, run_cli, tmux_patch
from claudeteam.runtime import paths, watchdog


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


class _FakePopenProc:
    """subprocess.Popen-shaped fake good enough for both `watchdog.respawn`
    (which discards the proc) and `watchdog.list_orphan_pids` →
    `subprocess.run` (which uses Popen as context manager and calls
    poll/communicate/wait/kill on the result). Round-65 round-67: hoisted
    to module level so `_fake_popen` and `silent_popen` share one
    Popen-contract surface — fixing a subprocess-internal contract change
    only needs touching one class."""

    def __init__(self, argv):
        self.argv = argv
        # subprocess.run reads `.args` on the Popen result internally
        # (e.g. when constructing CompletedProcess). Without it any
        # subprocess.run call routed through this fake AttributeError's.
        self.args = argv
        self.returncode = 0
        self.stdout = ""
        self.stderr = ""

    def __enter__(self): return self
    def __exit__(self, *exc): return False
    def communicate(self, *a, **kw): return (self.stdout, self.stderr)
    def wait(self, *a, **kw): return self.returncode
    def poll(self): return self.returncode
    def kill(self): return None


@contextlib.contextmanager
def _fake_popen():
    """Replace subprocess.Popen used by up.py."""
    calls = []

    def fake_popen(argv, *args, **kwargs):
        calls.append(list(argv))
        # Simulate the daemon writing its pid file
        if argv[:2] == ["claudeteam", "router"]:
            paths.ensure_state_dir()
            paths.router_pid_file().write_text("12345", encoding="utf-8")
        elif argv[:2] == ["claudeteam", "watchdog"]:
            paths.ensure_state_dir()
            paths.watchdog_pid_file().write_text("12346", encoding="utf-8")
        return _FakePopenProc(argv)

    with attr_patch(subprocess, Popen=fake_popen):
        yield calls


def _fake_alive(answers):
    """Make watchdog.is_alive return successive scripted booleans."""
    iterator = iter(answers)

    def fake(spec, **kwargs):
        try:
            return next(iterator)
        except StopIteration:
            return False

    return attr_patch(watchdog, is_alive=fake)


# ── up ──────────────────────────────────────────────────────────


def test_up_starts_session_and_spawns_two_daemons():
    team = {"session": "S",
            "agents": {"manager": {"cli": "claude-code"}}}
    # capture_pane returns a string with claude-code's ready marker so
    # start.py's wake.wait_until_ready short-circuits without polling.
    extras = tmux_patch(
        new_session=lambda *a, **kw: True,
        new_window=lambda *a, **kw: True,
        spawn_agent=lambda *a, **kw: True,
        capture_pane=lambda target, lines=80: "bypass permissions on\n? for shortcuts\n>",
        inject=lambda *a, **kw: True,
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


def test_up_returns_one_when_daemon_fast_fails_no_pid_file():
    """REGRESSION (round-62, real bug): a daemon that fast-fails at
    startup (e.g. chat_id missing in runtime_config) error_exits
    BEFORE writing its pid file. up.py used to print
    '⚠️ launched but no pid file yet' and STILL return 0, masking
    the boot failure. Now treats absence-of-pidfile as failure."""
    team = {"session": "S", "agents": {"manager": {}}}

    def silent_popen(argv, *args, **kwargs):
        # Popen succeeds but the daemon fast-fails — never writes a pid
        return _FakePopenProc(argv)

    with isolated_env(team=team), _fake_tmux(session_alive=True), \
            attr_patch(subprocess, Popen=silent_popen), \
            _fake_alive([False, False]):
        rc, out, err = run_cli(["up"])
    # Must return non-zero; warning explains what to do
    assert rc != 0, f"up rc={rc} should be non-zero on no-pid-file"
    combined = out + err
    assert "didn't write a pid file" in combined
    assert "fast-failed at startup" in combined


def test_up_warns_when_daemon_spawn_fails():
    """REGRESSION (round 7 D4): up was printing '✅ team up' even when
    router/watchdog Popen raised OSError (e.g. 'claudeteam' not on PATH).
    Now must say 'team up with errors' and return non-zero."""
    team = {"session": "S", "agents": {"manager": {}}}

    def boom_popen(argv, *args, **kwargs):
        raise OSError(2, "No such file or directory: 'claudeteam'")

    with isolated_env(team=team), _fake_tmux(session_alive=True), \
            attr_patch(subprocess, Popen=boom_popen), \
            _fake_alive([False, False]):
        rc, out, err = run_cli(["up"])
        assert rc != 0
        # Specific failure shown
        assert "failed to spawn" in (out + err)
        # Footer reflects the failure, NOT "✅ team up"
        assert "✅ team up" not in out
        assert "team up with errors" in out


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

        with attr_patch(os, kill=fake_kill):
            rc, out, _ = run_cli(["down"])
        assert rc == 0
        assert "router: pid" in out and "stopped" in out
        assert "watchdog: pid" in out
        assert tx["session_killed"]
        assert not paths.router_pid_file().exists()
        assert not paths.watchdog_pid_file().exists()


def test_down_handles_already_dead_pid():
    team = {"session": "S", "agents": {"manager": {}}}
    with isolated_env(team=team), _fake_tmux(session_alive=False):
        paths.ensure_state_dir()
        paths.router_pid_file().write_text("99999", encoding="utf-8")

        def fake_kill(pid, sig):
            raise ProcessLookupError()

        with attr_patch(os, kill=fake_kill):
            rc, out, _ = run_cli(["down"])
        assert rc == 0
        assert "already dead" in out
        assert not paths.router_pid_file().exists()


def test_down_help():
    rc, out, _ = run_cli(["down", "--help"])
    assert rc == 0
    assert "usage: claudeteam down" in out


def test_down_handles_corrupt_pid_file():
    """A pid file with non-int garbage (e.g. partial write from a crash)
    should be removed and not blow up the down sequence — this is the
    pidlock.read_pid → None branch."""
    team = {"session": "S", "agents": {"manager": {}}}
    with isolated_env(team=team), _fake_tmux(session_alive=False):
        paths.ensure_state_dir()
        paths.router_pid_file().write_text("garbage-not-an-int", encoding="utf-8")

        rc, out, _ = run_cli(["down"])
        assert rc == 0
        assert "corrupt pid file" in out
        assert not paths.router_pid_file().exists()


def test_down_returns_one_when_pid_refuses_to_die():
    """SIGTERM delivered, then SIGKILL escalation, then surface the
    warning — when both signals appear ineffective (kill -0 keeps
    succeeding), down should warn + return non-zero. Smoke v3 bumped
    grace 3s→10s SIGTERM + 2s post-SIGKILL = 12s total."""
    import time as _time
    team = {"session": "S", "agents": {"manager": {}}}
    with isolated_env(team=team), _fake_tmux(session_alive=False):
        paths.ensure_state_dir()
        paths.router_pid_file().write_text("99999", encoding="utf-8")

        signals_seen = []
        def fake_kill(pid, sig):
            signals_seen.append(sig)
            return None

        with attr_patch(os, kill=fake_kill), attr_patch(_time, sleep=lambda _s: None):
            rc, _, err = run_cli(["down"])
        assert rc != 0
        assert "still alive" in err
        # pid file is NOT removed — operator needs to investigate
        assert paths.router_pid_file().exists()
        # Both SIGTERM and SIGKILL must have been attempted (escalation
        # loop survived the smoke-v3 finding that 3s SIGTERM-only grace
        # left daemons orphaned).
        import signal as _signal
        assert _signal.SIGTERM in signals_seen
        assert _signal.SIGKILL in signals_seen
