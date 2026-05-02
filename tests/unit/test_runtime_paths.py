"""Tests for runtime/paths.py — env-driven state directory layout."""
from __future__ import annotations

import contextlib
import os
import tempfile
from pathlib import Path

from claudeteam.runtime import paths


@contextlib.contextmanager
def _state_env(value):
    old = os.environ.get("CLAUDETEAM_STATE_DIR")
    if value is None:
        os.environ.pop("CLAUDETEAM_STATE_DIR", None)
    else:
        os.environ["CLAUDETEAM_STATE_DIR"] = str(value)
    try:
        yield
    finally:
        if old is None:
            os.environ.pop("CLAUDETEAM_STATE_DIR", None)
        else:
            os.environ["CLAUDETEAM_STATE_DIR"] = old


def test_state_dir_falls_back_to_home_when_env_unset():
    with _state_env(None):
        assert paths.state_dir() == Path.home() / ".claudeteam"


def test_state_dir_uses_env_when_set():
    with tempfile.TemporaryDirectory() as tmp:
        with _state_env(tmp):
            assert paths.state_dir() == Path(tmp)


def test_facts_dir_is_state_subdir():
    with tempfile.TemporaryDirectory() as tmp:
        with _state_env(tmp):
            assert paths.facts_dir() == Path(tmp) / "facts"


def test_state_file_returns_path_without_mkdir():
    with tempfile.TemporaryDirectory() as tmp:
        with _state_env(tmp):
            p = paths.state_file("nested/deep/file.txt")
            assert p == Path(tmp) / "nested" / "deep" / "file.txt"
            # pure path resolution — no I/O side effect
            assert not p.parent.exists()


def test_ensure_state_dir_creates_when_missing():
    with tempfile.TemporaryDirectory() as tmp:
        sd = Path(tmp) / "state"
        with _state_env(sd):
            assert not sd.exists()
            paths.ensure_state_dir()
            assert sd.exists()


def test_named_pid_files_land_in_state_dir():
    with tempfile.TemporaryDirectory() as tmp:
        with _state_env(tmp):
            assert paths.router_pid_file() == Path(tmp) / "router.pid"
            assert paths.watchdog_pid_file() == Path(tmp) / "watchdog.pid"
            assert paths.router_cursor_file() == Path(tmp) / "router.cursor"


def test_state_dir_re_reads_env_each_call():
    with tempfile.TemporaryDirectory() as tmp1, tempfile.TemporaryDirectory() as tmp2:
        with _state_env(tmp1):
            assert paths.state_dir() == Path(tmp1)
        with _state_env(tmp2):
            assert paths.state_dir() == Path(tmp2)
