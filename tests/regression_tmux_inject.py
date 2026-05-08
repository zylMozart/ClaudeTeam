#!/usr/bin/env python3
"""Regression tests for pane-diff idle detection (C1-C8 + legacy fallback).

Mirrors ClaudeTeam scripts/regression_tmux_inject.py per architect_pane_diff_idle_2026-04-25 §6.
"""
import os
import sys
import time

# 让 is_agent_idle 在测试里走"快路径"——pane-diff 默认 10×300ms ≈ 3s。
os.environ.setdefault("CLAUDETEAM_IDLE_SAMPLE_COUNT", "2")
os.environ.setdefault("CLAUDETEAM_IDLE_SAMPLE_INTERVAL_MS", "0")

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
for p in (os.path.join(_ROOT, "src"), _ROOT):
    if p not in sys.path:
        sys.path.insert(0, p)

from claudeteam.runtime import tmux_utils  # noqa: E402


class R:
    def __init__(self, returncode=0, stdout="", stderr=""):
        self.returncode = returncode
        self.stdout = stdout
        self.stderr = stderr


class Patch:
    def __init__(self, module, **items):
        self.module = module
        self.items = items
        self.old = {}

    def __enter__(self):
        for name, value in self.items.items():
            self.old[name] = getattr(self.module, name)
            setattr(self.module, name, value)

    def __exit__(self, exc_type, exc, tb):
        for name, value in self.old.items():
            setattr(self.module, name, value)


def _make_capture(frames):
    """FakeCapture：第 N 次调用返回 frames[N-1]，越界保留最后一帧。"""
    state = {"i": 0}

    def call(*_args, **_kwargs):
        i = state["i"]
        state["i"] = i + 1
        if i < len(frames):
            return frames[i]
        return frames[-1] if frames else ""

    return call


def test_pane_diff_C1_static_idle():
    frames = ["gpt-5 default\nLine A\n❯ "] * 10
    with Patch(tmux_utils, capture_pane=_make_capture(frames)):
        assert tmux_utils.is_agent_idle(
            "s", "w", sample_count=10, sample_interval_ms=0) is True


def test_pane_diff_C2_spinner_busy():
    spinner_chars = ["⣾", "⣽", "⣻", "⢿", "⡿", "⣟", "⣯", "⣷", "⣾", "⣽"]
    frames = [f"header\n{c} Thinking\n❯ \n" for c in spinner_chars]
    with Patch(tmux_utils, capture_pane=_make_capture(frames)):
        assert tmux_utils.is_agent_idle(
            "s", "w", sample_count=10, sample_interval_ms=0) is False


def test_pane_diff_C3_timestamp_drift_idle():
    frames = [f"header\nbaked for 1m {16+i}s\n❯ " for i in range(10)]
    with Patch(tmux_utils, capture_pane=_make_capture(frames)):
        assert tmux_utils.is_agent_idle(
            "s", "w", sample_count=10, sample_interval_ms=0) is True


def test_pane_diff_C4_capture_failure_busy():
    frames = ["header\nidle\n❯ "] * 4 + [""] + ["header\nidle\n❯ "] * 5
    with Patch(tmux_utils, capture_pane=_make_capture(frames)):
        assert tmux_utils.is_agent_idle(
            "s", "w", sample_count=10, sample_interval_ms=0) is False


def test_pane_diff_C5_cursor_jitter_idle():
    frames = [f"static line A\nstatic line B\n❯ pos abc{i}xyz" for i in range(10)]
    with Patch(tmux_utils, capture_pane=_make_capture(frames)):
        assert tmux_utils.is_agent_idle(
            "s", "w", sample_count=10, sample_interval_ms=0) is True


def test_pane_diff_C6_streaming_busy():
    frames = [f"streaming output\nincremental_{'x'*i}\n❯ " for i in range(10)]
    with Patch(tmux_utils, capture_pane=_make_capture(frames)):
        assert tmux_utils.is_agent_idle(
            "s", "w", sample_count=10, sample_interval_ms=0) is False


def test_pane_diff_C7_no_busy_marker_but_changing():
    frames = [f"applying patch\n+++ added line {chr(ord('a')+i)}\n❯ " for i in range(10)]
    with Patch(tmux_utils, capture_pane=_make_capture(frames)):
        assert tmux_utils.is_agent_idle(
            "s", "w", sample_count=10, sample_interval_ms=0) is False


def test_pane_diff_C8_quick_idle_hint_old_activity():
    old_ts = str(int(time.time() - 60))

    def fake_run(cmd, **kw):
        if "display-message" in cmd:
            return R(0, stdout=old_ts)
        return R(0)

    with Patch(tmux_utils, capture_pane=lambda *a, **k: "header\n❯ ready\n"):
        with Patch(tmux_utils.subprocess, run=fake_run):
            assert tmux_utils.quick_idle_hint("s", "w", max_age_secs=2) is True


def test_pane_diff_C8b_quick_idle_hint_recent_activity():
    fresh_ts = str(int(time.time()))

    def fake_run(cmd, **kw):
        if "display-message" in cmd:
            return R(0, stdout=fresh_ts)
        return R(0)

    with Patch(tmux_utils, capture_pane=lambda *a, **k: "header\n❯ \n"):
        with Patch(tmux_utils.subprocess, run=fake_run):
            assert tmux_utils.quick_idle_hint("s", "w", max_age_secs=2) is False


def test_pane_diff_C8c_quick_idle_hint_busy_marker_in_frame():
    old_ts = str(int(time.time() - 60))

    def fake_run(cmd, **kw):
        if "display-message" in cmd:
            return R(0, stdout=old_ts)
        return R(0)

    with Patch(tmux_utils, capture_pane=lambda *a, **k: "header\n⣾ Thinking\n"):
        with Patch(tmux_utils.subprocess, run=fake_run):
            assert tmux_utils.quick_idle_hint("s", "w", max_age_secs=2) is False


def test_legacy_env_fallback_busy_marker():
    os.environ["CLAUDETEAM_IDLE_LEGACY"] = "1"
    try:
        with Patch(tmux_utils, capture_pane=lambda *a, **k: "Thinking\n"):
            assert tmux_utils.is_agent_idle("s", "w") is False
        with Patch(tmux_utils, capture_pane=lambda *a, **k: "❯ \n"):
            assert tmux_utils.is_agent_idle("s", "w") is True
    finally:
        os.environ.pop("CLAUDETEAM_IDLE_LEGACY", None)


def main():
    test_pane_diff_C1_static_idle()
    test_pane_diff_C2_spinner_busy()
    test_pane_diff_C3_timestamp_drift_idle()
    test_pane_diff_C4_capture_failure_busy()
    test_pane_diff_C5_cursor_jitter_idle()
    test_pane_diff_C6_streaming_busy()
    test_pane_diff_C7_no_busy_marker_but_changing()
    test_pane_diff_C8_quick_idle_hint_old_activity()
    test_pane_diff_C8b_quick_idle_hint_recent_activity()
    test_pane_diff_C8c_quick_idle_hint_busy_marker_in_frame()
    test_legacy_env_fallback_busy_marker()
    print("✅ regression_tmux_inject (restructure) passed: pane-diff C1-C8 + legacy fallback")


if __name__ == "__main__":
    main()
