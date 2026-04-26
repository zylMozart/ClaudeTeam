#!/usr/bin/env python3
"""Unit tests for claudeteam.messaging.router.cursor helpers.

All tests are pure or use a temp directory; no subprocess, no live Feishu.
"""
from __future__ import annotations

import os
import sys
import tempfile
import time
from pathlib import Path

_ROOT = Path(__file__).resolve().parents[1]
for _p in (_ROOT / "src", _ROOT):
    if str(_p) not in sys.path:
        sys.path.insert(0, str(_p))

from claudeteam.messaging.router.cursor import (
    parse_cursor,
    load_cursor,
    save_cursor,
    refresh_heartbeat,
    parse_create_time,
)


# ── parse_cursor ──────────────────────────────────────────────────────────────

def test_parse_cursor_valid_float():
    assert parse_cursor("1776591454.123") == 1776591454.123


def test_parse_cursor_integer_string():
    result = parse_cursor("1776591454")
    assert result == 1776591454.0


def test_parse_cursor_empty_returns_none():
    assert parse_cursor("") is None
    assert parse_cursor("   ") is None


def test_parse_cursor_invalid_returns_none():
    assert parse_cursor("not-a-number") is None


# ── load_cursor ───────────────────────────────────────────────────────────────

def test_load_cursor_missing_files():
    assert load_cursor(["/nonexistent/a", "/nonexistent/b"]) is None


def test_load_cursor_reads_first_valid():
    with tempfile.TemporaryDirectory() as tmp:
        p = os.path.join(tmp, "cursor")
        with open(p, "w") as f:
            f.write("1234567890.0")
        result = load_cursor(["/nonexistent", p])
        assert result == 1234567890.0


# ── save_cursor ───────────────────────────────────────────────────────────────

def test_save_cursor_writes_file():
    with tempfile.TemporaryDirectory() as tmp:
        p = os.path.join(tmp, "cursor")
        assert save_cursor(p, 1234.5) is True
        with open(p) as f:
            content = f.read().strip()
        assert float(content) == pytest_approx(1234.5)


def test_save_cursor_monotonic_skip():
    with tempfile.TemporaryDirectory() as tmp:
        p = os.path.join(tmp, "cursor")
        save_cursor(p, 100.0)
        assert save_cursor(p, 99.0, current=100.0) is False
        assert save_cursor(p, 100.0, current=100.0) is False
        # Strictly greater → should write
        assert save_cursor(p, 101.0, current=100.0) is True


def test_save_cursor_no_current_always_writes():
    with tempfile.TemporaryDirectory() as tmp:
        p = os.path.join(tmp, "cursor")
        assert save_cursor(p, 50.0, current=None) is True


# ── refresh_heartbeat ─────────────────────────────────────────────────────────

def test_refresh_heartbeat_touches_existing_file():
    with tempfile.TemporaryDirectory() as tmp:
        p = os.path.join(tmp, "cursor")
        with open(p, "w") as f:
            f.write("100.0")
        old_mtime = os.path.getmtime(p)
        time.sleep(0.05)
        refresh_heartbeat(p)
        new_mtime = os.path.getmtime(p)
        assert new_mtime >= old_mtime


def test_refresh_heartbeat_creates_missing_file():
    with tempfile.TemporaryDirectory() as tmp:
        p = os.path.join(tmp, "cursor")
        assert not os.path.exists(p)
        refresh_heartbeat(p)
        assert os.path.exists(p)


# ── parse_create_time ─────────────────────────────────────────────────────────

def test_parse_create_time_ms_integer():
    result = parse_create_time("1776591454415")
    assert abs(result - 1776591454.415) < 1.0


def test_parse_create_time_seconds_float():
    result = parse_create_time("1776591454.415")
    assert abs(result - 1776591454.415) < 0.01


def test_parse_create_time_formatted_string():
    result = parse_create_time("2026-04-20 09:26")
    assert result is not None
    assert result > 1_700_000_000  # sanity: it's a real timestamp in recent years


def test_parse_create_time_invalid_returns_none():
    assert parse_create_time("not-a-time") is None


# ── helpers ───────────────────────────────────────────────────────────────────

def pytest_approx(x, rel=1e-6):
    """Tiny inline approx for use without pytest."""
    class _Approx:
        def __eq__(self, other):
            return abs(other - x) <= abs(x) * rel
    return _Approx()


# ── main ──────────────────────────────────────────────────────────────────────

def main() -> int:
    cases = [
        test_parse_cursor_valid_float,
        test_parse_cursor_integer_string,
        test_parse_cursor_empty_returns_none,
        test_parse_cursor_invalid_returns_none,
        test_load_cursor_missing_files,
        test_load_cursor_reads_first_valid,
        test_save_cursor_writes_file,
        test_save_cursor_monotonic_skip,
        test_save_cursor_no_current_always_writes,
        test_refresh_heartbeat_touches_existing_file,
        test_refresh_heartbeat_creates_missing_file,
        test_parse_create_time_ms_integer,
        test_parse_create_time_seconds_float,
        test_parse_create_time_formatted_string,
        test_parse_create_time_invalid_returns_none,
    ]
    passed = failed = 0
    for fn in cases:
        try:
            fn()
            print(f"  ✅ {fn.__name__}")
            passed += 1
        except Exception as exc:
            print(f"  ❌ {fn.__name__}: {exc}")
            failed += 1
    print(f"\nrouter cursor tests: {passed}/{passed + failed} passed")
    return 0 if failed == 0 else 1


if __name__ == "__main__":
    raise SystemExit(main())
