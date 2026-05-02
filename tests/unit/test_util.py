"""Tests for src/claudeteam/util.py — small shared helpers."""
from __future__ import annotations

import tempfile
from pathlib import Path

from claudeteam.util import (
    ago_ms, atomic_write_text, flock, fmt_time_ms, help_requested,
    now_ms, pop_flag, read_json, usage_error,
)


# ── now_ms ──────────────────────────────────────────────────────


def test_now_ms_returns_milliseconds():
    import time as _t
    before = int(_t.time() * 1000)
    n = now_ms()
    after = int(_t.time() * 1000)
    assert before <= n <= after


# ── fmt_time_ms ─────────────────────────────────────────────────


def test_fmt_time_ms_returns_question_for_zero():
    assert fmt_time_ms(0) == "?"


def test_fmt_time_ms_default_format_is_minute_precision():
    # 2026-01-15 14:30:00 local time → ms epoch
    import time as _t
    epoch = int(_t.mktime((2026, 1, 15, 14, 30, 0, 0, 0, -1))) * 1000
    out = fmt_time_ms(epoch)
    assert "01-15" in out and "14:30" in out
    assert ":00" not in out  # no seconds in default fmt


def test_fmt_time_ms_custom_format_includes_seconds():
    import time as _t
    epoch = int(_t.mktime((2026, 1, 15, 14, 30, 45, 0, 0, -1))) * 1000
    out = fmt_time_ms(epoch, fmt="%m-%d %H:%M:%S")
    assert "14:30:45" in out


# ── ago_ms ──────────────────────────────────────────────────────


def test_ago_ms_returns_question_for_zero_or_falsy():
    assert ago_ms(0) == "?"
    assert ago_ms(None) == "?"  # type: ignore[arg-type]


def test_ago_ms_seconds_under_60():
    # ms = 30000 means 30 seconds ago when now=60s
    assert ago_ms(30 * 1000, now=60.0) == "30s ago"
    assert ago_ms(0 * 1000 + 1, now=1.0) == "0s ago"


def test_ago_ms_minutes_between_60_and_3600():
    # ms = 0 means 90s ago when now=90; that's 1m
    assert ago_ms(1, now=90.0) == "1m ago"
    assert ago_ms(1, now=300.0) == "4m ago"


def test_ago_ms_hours_between_3600_and_86400():
    # ms encodes 1s; now is 2h+1s later → delta = 7200s → "2h ago"
    assert ago_ms(1000, now=7201.0) == "2h ago"


def test_ago_ms_days_above_86400():
    # ms = 1s; now = 3 days + 1s later → delta = 259200s → "3d ago"
    assert ago_ms(1000, now=259201.0) == "3d ago"


def test_ago_ms_clamps_to_zero_when_now_is_earlier_than_ms():
    # negative durations clamp to 0s
    assert ago_ms(10000, now=5.0) == "0s ago"


# ── atomic_write_text ───────────────────────────────────────────


def test_atomic_write_creates_file_and_writes_content():
    with tempfile.TemporaryDirectory() as tmp:
        target = Path(tmp) / "out.txt"
        atomic_write_text(target, "hello")
        assert target.read_text(encoding="utf-8") == "hello"


def test_atomic_write_creates_parent_dirs():
    with tempfile.TemporaryDirectory() as tmp:
        target = Path(tmp) / "deep" / "nested" / "dir" / "out.txt"
        atomic_write_text(target, "x")
        assert target.exists()
        assert target.parent.exists()


def test_atomic_write_overwrites_via_rename():
    with tempfile.TemporaryDirectory() as tmp:
        target = Path(tmp) / "out.txt"
        target.write_text("old", encoding="utf-8")
        atomic_write_text(target, "new")
        assert target.read_text(encoding="utf-8") == "new"


def test_atomic_write_leaves_no_tmp_after_success():
    with tempfile.TemporaryDirectory() as tmp:
        target = Path(tmp) / "out.txt"
        atomic_write_text(target, "x")
        sibling = list(Path(tmp).iterdir())
        assert len(sibling) == 1 and sibling[0].name == "out.txt"


def test_atomic_write_clobbers_stale_tmp_from_previous_crash():
    """Simulate a crash that left a .tmp behind; next call must succeed."""
    with tempfile.TemporaryDirectory() as tmp:
        target = Path(tmp) / "out.txt"
        # leftover from "previous crash"
        (target.with_suffix(".txt.tmp")).write_text("stale", encoding="utf-8")
        atomic_write_text(target, "fresh")
        assert target.read_text(encoding="utf-8") == "fresh"


# ── usage_error ─────────────────────────────────────────────────


def test_usage_error_prints_to_stderr_and_returns_one():
    import contextlib, io
    err = io.StringIO()
    with contextlib.redirect_stderr(err):
        rc = usage_error("usage: foo bar")
    assert rc == 1
    assert err.getvalue().strip() == "usage: foo bar"


# ── help_requested ──────────────────────────────────────────────


def test_help_requested_true_for_short_and_long():
    assert help_requested(["-h"]) is True
    assert help_requested(["--help"]) is True
    assert help_requested(["foo", "-h", "bar"]) is True


def test_help_requested_false_for_unrelated_args():
    assert help_requested([]) is False
    assert help_requested(["foo", "bar"]) is False
    assert help_requested(["-help"]) is False  # not a recognised form


# ── pop_flag ────────────────────────────────────────────────────


def test_pop_flag_returns_value_and_removes_pair():
    rest = ["foo", "--by", "manager", "bar"]
    assert pop_flag(rest, "--by") == "manager"
    assert rest == ["foo", "bar"]


def test_pop_flag_returns_none_when_absent():
    rest = ["a", "b"]
    assert pop_flag(rest, "--by") is None
    assert rest == ["a", "b"]


def test_pop_flag_returns_none_when_value_missing_at_end():
    rest = ["a", "--by"]
    assert pop_flag(rest, "--by") is None
    # rest is unchanged so caller can flag the user error
    assert rest == ["a", "--by"]


def test_pop_flag_handles_repeated_flag_takes_first():
    rest = ["--by", "alice", "--by", "bob"]
    assert pop_flag(rest, "--by") == "alice"
    assert rest == ["--by", "bob"]


# ── read_json ───────────────────────────────────────────────────


def test_read_json_returns_default_when_file_missing():
    with tempfile.TemporaryDirectory() as tmp:
        path = Path(tmp) / "missing.json"
        assert read_json(path, {}) == {}
        assert read_json(path, {"a": 1}) == {"a": 1}
        assert read_json(path, []) == []


def test_read_json_parses_existing_file():
    with tempfile.TemporaryDirectory() as tmp:
        path = Path(tmp) / "data.json"
        atomic_write_text(path, '{"k": "v"}')
        assert read_json(path, {}) == {"k": "v"}


def test_read_json_propagates_decode_error():
    """Caller should get the JSONDecodeError on corrupt files; read_json
    doesn't try to be clever."""
    import json as _json
    with tempfile.TemporaryDirectory() as tmp:
        path = Path(tmp) / "bad.json"
        path.write_text("not json", encoding="utf-8")
        try:
            read_json(path, {})
        except _json.JSONDecodeError:
            return
        raise AssertionError("expected JSONDecodeError")


# ── flock ───────────────────────────────────────────────────────


def test_flock_creates_parent_and_yields():
    with tempfile.TemporaryDirectory() as tmp:
        target = Path(tmp) / "deep" / "lock"
        with flock(target):
            assert target.exists()  # lock file got created


def test_flock_releases_on_normal_exit():
    """After the contextmanager exits, the lock file is unlocked but
    still present on disk (lock files persist; only the kernel lock
    state goes away)."""
    with tempfile.TemporaryDirectory() as tmp:
        target = Path(tmp) / "lock"
        with flock(target):
            pass
        assert target.exists()
        # we can re-acquire immediately
        with flock(target):
            pass


def test_flock_releases_on_exception():
    with tempfile.TemporaryDirectory() as tmp:
        target = Path(tmp) / "lock"
        try:
            with flock(target):
                raise RuntimeError("boom")
        except RuntimeError:
            pass
        # next acquire must succeed without hanging
        with flock(target):
            pass


# ── tmux_patch (helpers) ────────────────────────────────────────


def test_tmux_patch_replaces_and_restores():
    from helpers import tmux_patch
    from claudeteam.runtime import tmux

    real = tmux.has_session
    with tmux_patch(has_session=lambda s: True):
        assert tmux.has_session("anything") is True
    assert tmux.has_session is real


def test_tmux_patch_restores_even_on_exception():
    from helpers import tmux_patch
    from claudeteam.runtime import tmux

    real = tmux.has_session
    try:
        with tmux_patch(has_session=lambda s: True):
            raise RuntimeError("boom")
    except RuntimeError:
        pass
    assert tmux.has_session is real


def test_atomic_write_respects_encoding_arg():
    with tempfile.TemporaryDirectory() as tmp:
        target = Path(tmp) / "out.txt"
        atomic_write_text(target, "中文", encoding="utf-8")
        assert target.read_bytes().decode("utf-8") == "中文"
