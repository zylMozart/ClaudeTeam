"""Tests for src/claudeteam/util.py — small shared helpers."""
from __future__ import annotations

from claudeteam.util import ago_ms


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
