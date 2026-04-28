#!/usr/bin/env python3
"""Tests for post-compact identity re-read scheduling."""
import os
import sys
import time
import threading

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(_ROOT, "src"))

from claudeteam.commands.slash.tmux_ import _schedule_post_compact_reread

passed = failed = 0


def ok(name):
    global passed
    passed += 1
    print(f"  \u2705 {name}")


def fail(name, msg=""):
    global failed
    failed += 1
    print(f"  \u274c {name}: {msg}")


def test_schedule_spawns_thread():
    initial_count = threading.active_count()
    _schedule_post_compact_reread("fake-session", "test-agent")
    time.sleep(0.1)
    assert threading.active_count() > initial_count, "should spawn a background thread"
    ok("schedule_spawns_thread")


def test_schedule_does_not_block():
    t0 = time.monotonic()
    _schedule_post_compact_reread("fake-session", "test-agent")
    elapsed = time.monotonic() - t0
    assert elapsed < 1.0, f"should return immediately, took {elapsed:.2f}s"
    ok("schedule_does_not_block")


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
    print(f"\ncompact reread tests: {passed}/{total} passed")
    if failed:
        sys.exit(1)
