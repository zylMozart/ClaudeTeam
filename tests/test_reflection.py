#!/usr/bin/env python3
"""Tests for boss message counter and reflection meeting trigger."""
import json
import os
import sys
import tempfile

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(_ROOT, "src"))

from claudeteam.messaging.router.reflection import (
    load_counter,
    save_counter,
    increment_and_check,
    reset_counter,
    build_reflection_prompt,
    REFLECTION_THRESHOLD,
)

passed = failed = 0


def ok(name):
    global passed
    passed += 1
    print(f"  \u2705 {name}")


def fail(name, msg=""):
    global failed
    failed += 1
    print(f"  \u274c {name}: {msg}")


def test_load_empty():
    with tempfile.TemporaryDirectory() as d:
        data = load_counter(d)
        assert data["count"] == 0, data
        assert data["last_meeting_ts"] == 0, data
    ok("load_empty")


def test_save_and_load():
    with tempfile.TemporaryDirectory() as d:
        save_counter(d, {"count": 15, "last_meeting_ts": 1000.0})
        data = load_counter(d)
        assert data["count"] == 15, data
        assert data["last_meeting_ts"] == 1000.0, data
    ok("save_and_load")


def test_increment_below_threshold():
    with tempfile.TemporaryDirectory() as d:
        for i in range(REFLECTION_THRESHOLD - 1):
            reached = increment_and_check(d)
            assert not reached, f"should not reach at {i+1}"
        ok("increment_below_threshold")


def test_increment_reaches_threshold():
    with tempfile.TemporaryDirectory() as d:
        for i in range(REFLECTION_THRESHOLD - 1):
            increment_and_check(d)
        reached = increment_and_check(d)
        assert reached, "should reach threshold"
    ok("increment_reaches_threshold")


def test_custom_threshold():
    with tempfile.TemporaryDirectory() as d:
        for i in range(4):
            increment_and_check(d, threshold=5)
        reached = increment_and_check(d, threshold=5)
        assert reached, "should reach custom threshold of 5"
    ok("custom_threshold")


def test_reset_counter():
    with tempfile.TemporaryDirectory() as d:
        save_counter(d, {"count": 25, "last_meeting_ts": 0})
        reset_counter(d)
        data = load_counter(d)
        assert data["count"] == 0, data
        assert data["last_meeting_ts"] > 0, data
    ok("reset_counter")


def test_build_reflection_prompt():
    prompt = build_reflection_prompt("toolsmith", 30)
    assert "30" in prompt, prompt
    assert "toolsmith" in prompt, prompt
    assert "feishu_msg.py" in prompt, prompt
    ok("build_reflection_prompt")


def test_counter_survives_corruption():
    with tempfile.TemporaryDirectory() as d:
        path = os.path.join(d, "boss_msg_counter.json")
        with open(path, "w") as f:
            f.write("not valid json")
        data = load_counter(d)
        assert data["count"] == 0, data
    ok("counter_survives_corruption")


if __name__ == "__main__":
    for name, fn in list(globals().items()):
        if name.startswith("test_") and callable(fn):
            try:
                fn()
            except AssertionError as e:
                fail(name, str(e))
            except Exception as e:
                fail(name, f"exception: {e}")
    total = passed + failed
    print(f"\nreflection tests: {passed}/{total} passed")
    if failed:
        sys.exit(1)
