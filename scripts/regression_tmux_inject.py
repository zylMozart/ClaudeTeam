#!/usr/bin/env python3
"""Regression tests for tmux injection safety."""
import os
import sys

_SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, _SCRIPT_DIR)
_SRC_DIR = os.path.join(os.path.dirname(_SCRIPT_DIR), "src")
if _SRC_DIR not in sys.path:
    sys.path.insert(0, _SRC_DIR)
import tmux_inject_diagnose
from claudeteam.runtime import tmux_utils


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


def test_unsafe_input_not_injected():
    calls = []

    def fake_run(cmd, **kwargs):
        calls.append(cmd)
        return R(0)

    with Patch(tmux_utils, capture_pane=lambda s, w: "header\n› Run /review on my current changes\n"):
        with Patch(tmux_utils.subprocess, run=fake_run):
            result = tmux_utils.inject_when_idle("s", "manager", "hello", wait_secs=0)
    assert result.unsafe_input
    assert not result.submitted
    assert not any("send-keys" in c for cmd in calls for c in cmd), calls


def test_busy_not_forced():
    calls = []
    old_legacy = os.environ.get("CLAUDETEAM_IDLE_LEGACY")

    def fake_run(cmd, **kwargs):
        calls.append(cmd)
        return R(0)

    try:
        os.environ["CLAUDETEAM_IDLE_LEGACY"] = "1"
        with Patch(tmux_utils, capture_pane=lambda s, w: "Thinking\n"):
            with Patch(tmux_utils.subprocess, run=fake_run):
                result = tmux_utils.inject_when_idle(
                    "s", "manager", "hello", wait_secs=0.1, force_after_wait=False)
    finally:
        if old_legacy is None:
            os.environ.pop("CLAUDETEAM_IDLE_LEGACY", None)
        else:
            os.environ["CLAUDETEAM_IDLE_LEGACY"] = old_legacy
    assert result.busy_before
    assert result.error == "pane busy"
    assert not any(cmd[:1] == ["tmux"] and "send-keys" in cmd for cmd in calls), calls


def test_idle_injects_and_submits():
    calls = []

    def fake_run(cmd, **kwargs):
        calls.append(cmd)
        return R(0)

    with Patch(tmux_utils, capture_pane=lambda s, w: "gpt-5 default\n› \n"):
        with Patch(tmux_utils.subprocess, run=fake_run):
            result = tmux_utils.inject_when_idle(
                "s", "manager", "hello", wait_secs=0.1, force_after_wait=False)
    assert result.ok
    assert result.submitted
    assert not result.residual_visible
    assert any(cmd[:3] == ["tmux", "send-keys", "-l"] for cmd in calls), calls
    assert any(cmd[-1:] == ["Enter"] for cmd in calls), calls
    assert any(cmd[-1:] == ["C-m"] for cmd in calls), calls


def test_submitted_history_is_not_residual():
    calls = {"capture": 0}

    def fake_capture(_session, _window):
        calls["capture"] += 1
        if calls["capture"] < 3:
            return "gpt-5 default\n› \n"
        return "› hello\n\n• ok\n\n› Implement {feature}\n"

    def fake_run(_cmd, **_kwargs):
        return R(0)

    with Patch(tmux_utils, capture_pane=fake_capture):
        with Patch(tmux_utils.subprocess, run=fake_run):
            result = tmux_utils.inject_when_idle(
                "s", "manager", "hello", wait_secs=0.1, force_after_wait=False)
    assert result.ok
    assert result.submitted
    assert not result.residual_visible


def test_diagnose_reports_agent_and_tail():
    panes = {
        ("s", "manager"): "old\n› Run /review on my current changes\n",
        ("s", "worker"): "gpt-5 default\n› \n",
    }

    with Patch(tmux_inject_diagnose, capture_pane=lambda s, w: panes.get((s, w), "")):
        rows = tmux_inject_diagnose.scan("s", ["manager", "worker"])
    assert len(rows) == 1
    assert rows[0]["agent"] == "manager"
    assert rows[0]["pane"] == "s:manager"
    assert "Run /review" in rows[0]["residual"]
    assert "Run /review" in rows[0]["tail"]


def main():
    old_legacy = os.environ.get("CLAUDETEAM_IDLE_LEGACY")
    try:
        os.environ["CLAUDETEAM_IDLE_LEGACY"] = "1"
        test_unsafe_input_not_injected()
        test_busy_not_forced()
        test_idle_injects_and_submits()
        test_submitted_history_is_not_residual()
        test_diagnose_reports_agent_and_tail()
    finally:
        if old_legacy is None:
            os.environ.pop("CLAUDETEAM_IDLE_LEGACY", None)
        else:
            os.environ["CLAUDETEAM_IDLE_LEGACY"] = old_legacy
    print("✅ regression_tmux_inject passed")


if __name__ == "__main__":
    main()
