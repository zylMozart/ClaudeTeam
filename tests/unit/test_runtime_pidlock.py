"""Tests for runtime/pidlock.py — single-instance daemon lock."""
from __future__ import annotations

import os

from helpers import isolated_env
from claudeteam.runtime import paths, pidlock


# ── acquire ─────────────────────────────────────────────────────


def test_acquire_writes_current_pid_when_file_missing():
    with isolated_env():
        pf = paths.router_pid_file()
        assert not pf.exists()
        assert pidlock.acquire(pf) is True
        assert pf.read_text(encoding="utf-8").strip() == str(os.getpid())


def test_acquire_overwrites_stale_pid_file():
    """A pid file pointing at a dead pid is treated as stale."""
    with isolated_env():
        pf = paths.router_pid_file()
        paths.ensure_state_dir()
        pf.write_text("99999", encoding="utf-8")  # almost certainly dead

        # patch os.kill to simulate "no such process"
        saved = os.kill

        def fake_kill(pid, sig):
            if pid == 99999:
                raise ProcessLookupError()
            return saved(pid, sig)

        os.kill = fake_kill
        try:
            assert pidlock.acquire(pf) is True
        finally:
            os.kill = saved
        assert pf.read_text(encoding="utf-8").strip() == str(os.getpid())


def test_acquire_refuses_when_live_process_holds_lock():
    """When the recorded pid IS alive, return False without overwriting."""
    with isolated_env():
        pf = paths.router_pid_file()
        paths.ensure_state_dir()
        # use *our* pid as the holder — guaranteed alive
        pf.write_text(str(os.getpid()), encoding="utf-8")
        assert pidlock.acquire(pf, name="router") is False
        # didn't overwrite (still our pid; trivially equal)
        assert pf.read_text(encoding="utf-8").strip() == str(os.getpid())


def test_acquire_creates_state_dir_lazily():
    with isolated_env() as tmp:
        sd = tmp / "state"
        assert not sd.exists()
        pidlock.acquire(paths.router_pid_file())
        assert sd.exists()


def test_acquire_handles_garbage_pid_file_as_stale():
    with isolated_env():
        pf = paths.router_pid_file()
        paths.ensure_state_dir()
        pf.write_text("not-a-number", encoding="utf-8")
        assert pidlock.acquire(pf) is True
        assert pf.read_text(encoding="utf-8").strip() == str(os.getpid())


# ── release ─────────────────────────────────────────────────────


def test_release_unlinks_when_we_own():
    with isolated_env():
        pf = paths.router_pid_file()
        pidlock.acquire(pf)
        assert pf.exists()
        pidlock.release(pf)
        assert not pf.exists()


def test_release_skips_when_pid_belongs_to_someone_else():
    with isolated_env():
        pf = paths.router_pid_file()
        paths.ensure_state_dir()
        pf.write_text("12345", encoding="utf-8")  # not ours
        pidlock.release(pf)
        # untouched
        assert pf.exists()
        assert pf.read_text(encoding="utf-8").strip() == "12345"


def test_release_is_safe_when_file_missing():
    with isolated_env():
        pf = paths.router_pid_file()
        assert not pf.exists()
        pidlock.release(pf)  # must not raise
