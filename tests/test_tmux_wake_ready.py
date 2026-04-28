#!/usr/bin/env python3
"""Unit tests for tmux wake readiness and forced injection safety."""
from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

from claudeteam.runtime import tmux_utils


class R:
    def __init__(self, returncode=0, stdout="", stderr=""):
        self.returncode = returncode
        self.stdout = stdout
        self.stderr = stderr


class Patch:
    def __init__(self, obj, **items):
        self.obj = obj
        self.items = items
        self.old = {}

    def __enter__(self):
        for key, value in self.items.items():
            self.old[key] = getattr(self.obj, key)
            setattr(self.obj, key, value)

    def __exit__(self, exc_type, exc, tb):
        for key, value in self.old.items():
            setattr(self.obj, key, value)


def test_ready_result_ready_marker():
    result = tmux_utils.wait_cli_ui_ready(
        lambda: "banner\n? for shortcuts\n> ",
        ["? for shortcuts"],
        timeout_s=0.1,
    )
    assert result.ok
    assert result.reason == "ready"
    assert result.matched_marker == "? for shortcuts"


def test_ready_result_shell_prompt():
    result = tmux_utils.wait_cli_ui_ready(
        lambda: "admin@host:/app$ ",
        ["? for shortcuts"],
        timeout_s=0.1,
    )
    assert not result.ok
    assert result.reason == "shell_prompt"


def test_ready_result_auth_required():
    result = tmux_utils.wait_cli_ui_ready(
        lambda: "Please log in to continue",
        ["? for shortcuts"],
        timeout_s=0.1,
    )
    assert not result.ok
    assert result.reason == "auth_required"


def test_detect_unsubmitted_input_ignores_history_above_empty_prompt():
    pane = "old prompt text\n──────────────── devops ──\n❯\u00a0\n────────────────\n  ⏵⏵ bypass permissions on (shift+tab to cycle)\n"
    assert tmux_utils.detect_unsubmitted_input_text(pane) == ""


def test_detect_unsubmitted_input_finds_current_non_empty_prompt():
    pane = "──────────────── devops ──\n❯ current draft\n────────────────\n"
    assert tmux_utils.detect_unsubmitted_input_text(pane) == "current draft"


def test_force_after_wait_refuses_shell_prompt():
    calls = []

    def fake_run(cmd, **kwargs):
        calls.append(cmd)
        return R(0)

    with Patch(tmux_utils, capture_pane=lambda s, w: "admin@host:/app$ "):
        with Patch(tmux_utils.subprocess, run=fake_run):
            result = tmux_utils.inject_when_idle(
                "s", "worker", "hello", wait_secs=0, force_after_wait=True)

    assert result.unsafe_input
    assert result.error == "unsafe forced injection target"
    assert not result.submitted
    assert not any(cmd[:3] == ["tmux", "send-keys", "-l"] for cmd in calls), calls


def main() -> int:
    tests = [v for k, v in sorted(globals().items()) if k.startswith("test_")]
    passed = failed = 0
    for fn in tests:
        try:
            fn()
            print(f"  ok {fn.__name__}")
            passed += 1
        except Exception as e:
            print(f"  fail {fn.__name__}: {e}")
            failed += 1
    print(f"\ntmux wake tests: {passed}/{passed + failed} passed")
    return 0 if failed == 0 else 1


if __name__ == "__main__":
    raise SystemExit(main())
