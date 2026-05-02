"""Tests for runtime/pidlock.py — single-instance daemon lock."""
from __future__ import annotations

import os
from pathlib import Path

from helpers import attr_patch, isolated_env
from claudeteam.runtime import paths, pidlock


def _seed_pid(value: str) -> Path:
    """Pre-populate router.pid with `value`. Returns the path."""
    pf = paths.router_pid_file()
    paths.ensure_state_dir()
    pf.write_text(value, encoding="utf-8")
    return pf


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
        pf = _seed_pid("99999")  # almost certainly dead

        # patch os.kill to simulate "no such process"
        real_kill = os.kill

        def fake_kill(pid, sig):
            if pid == 99999:
                raise ProcessLookupError()
            return real_kill(pid, sig)

        with attr_patch(os, kill=fake_kill):
            assert pidlock.acquire(pf) is True
        assert pf.read_text(encoding="utf-8").strip() == str(os.getpid())


def test_acquire_refuses_when_live_process_holds_lock():
    """When the recorded pid IS alive, return False without overwriting."""
    with isolated_env():
        # use *our* pid as the holder — guaranteed alive
        pf = _seed_pid(str(os.getpid()))
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
        pf = _seed_pid("not-a-number")
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
        pf = _seed_pid("12345")  # not ours
        pidlock.release(pf)
        # untouched
        assert pf.exists()
        assert pf.read_text(encoding="utf-8").strip() == "12345"


def test_release_is_safe_when_file_missing():
    with isolated_env():
        pf = paths.router_pid_file()
        assert not pf.exists()
        pidlock.release(pf)  # must not raise
