#!/usr/bin/env python3
"""Unit tests for unified agent state classification."""
from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

from claudeteam.runtime import agent_state
from claudeteam.runtime.agent_state import classify, classify_pane, pane_diff_classify


def _classify(buf: str, *, live_cli: bool = True, idle_hint=None, status=None):
    return classify_pane(
        "coder",
        buf,
        live_cli=live_cli,
        idle_hint=idle_hint,
        window_exists=bool(buf),
        status_table_state=status,
    )


def _assert(buf: str, code: str, *, live_cli: bool = True, idle_hint=None, status=None):
    state = _classify(buf, live_cli=live_cli, idle_hint=idle_hint, status=status)
    assert state.code == code, state
    return state


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


# §4.1 fixture coverage

def test_empty_pane_is_no_window():
    _assert("", "no_window", live_cli=False)


def test_host_shell_prompt_without_cli_is_not_running():
    _assert("admin@host:/srv/project$ ", "cli_not_running", live_cli=False)


def test_container_shell_prompt_without_cli_is_not_running():
    _assert("root@abc123:/app# ", "cli_not_running", live_cli=False)


def test_claude_idle_bypass_permissions():
    _assert("⏵⏵ bypass permissions on\n> ", "idle")


def test_claude_idle_shortcuts_marker():
    _assert("? for shortcuts\n> ", "idle")


def test_claude_idle_new_task_marker():
    _assert("new task?\n› ", "idle")


def test_busy_esc_to_interrupt():
    _assert("Working (12s · esc to interrupt)", "busy")


def test_busy_running_tool():
    _assert("Running tool Bash\n⣾", "busy")


def test_busy_spinner():
    _assert("Applying patch ⣽", "busy")


def test_busy_pane_diff_hint():
    _assert("some plain output", "busy", idle_hint=False)


def test_permission_question():
    _assert("Do you want to proceed?\n❯ 1. Yes\n  2. No", "permission")


def test_permission_choice_marker():
    _assert("Choose an option\n❯ 1. Allow\n  2. Deny", "permission")


def test_quota_limit_hit():
    _assert("you have hit your limit for this week", "quota")


def test_rate_limit():
    _assert("rate limit exceeded, try again later", "quota")


def test_compacting_conversation():
    _assert("Compacting conversation…", "compact")


def test_loading_configuration():
    _assert("Loading configuration", "waking")


def test_sleep_marker_without_cli():
    _assert("💤 coder 已休眠 (lazy-wake)", "sleep", live_cli=False)


def test_residual_input():
    _assert("? for shortcuts\n> please do not submit yet", "unsubmitted_input")


# §4.2 priority coverage

def test_history_busy_current_idle_is_idle():
    buf = "Running tool Bash\nesc to interrupt\nresult done\n? for shortcuts\n> "
    _assert(buf, "idle", idle_hint=True)


def test_dead_cli_with_idle_history_is_not_idle():
    buf = "? for shortcuts\n> "
    _assert(buf, "cli_not_running", live_cli=False)


def test_dead_cli_with_sleep_status_is_sleep():
    buf = "? for shortcuts\n> "
    _assert(buf, "sleep", live_cli=False, status="休眠")


def test_sleep_status_with_live_cli_is_inconsistent_not_sleep():
    state = _assert("? for shortcuts\n> ", "idle", live_cli=True, status="休眠")
    assert "不一致" in state.brief


def test_pane_diff_classify_busy_when_samples_change():
    samples = iter(["\x1b[31mspin 1", "\x1b[32mspin 2"])
    with Patch(agent_state, _capture_pane_raw=lambda session, agent: next(samples)):
        with Patch(agent_state.time, sleep=lambda _: None):
            assert pane_diff_classify("sess", "coder", samples=2, interval=0) is True


def test_pane_diff_classify_idle_when_samples_identical():
    with Patch(agent_state, _capture_pane_raw=lambda session, agent: "\x1b[31mstatic"):
        with Patch(agent_state.time, sleep=lambda _: None):
            assert pane_diff_classify("sess", "coder", samples=3, interval=0) is False


def test_classify_uses_pane_diff_busy_after_live_cli_check():
    with Patch(agent_state, _window_exists=lambda session, agent: True):
        with Patch(agent_state, capture_pane=lambda session, agent, lines=80: "? for shortcuts\n> "):
            with Patch(agent_state, _status_table_state=lambda agent: None):
                with Patch(agent_state, _pane_pid=lambda session, agent: 100):
                    with Patch(agent_state, _subtree_has_process=lambda pid, process_name: True):
                        with Patch(agent_state, pane_diff_classify=lambda session, agent: True):
                            state = classify("coder", "sess")
    assert state.code == "busy"
    assert state.idle_hint is False


def test_classify_uses_pane_diff_idle_after_live_cli_check():
    with Patch(agent_state, _window_exists=lambda session, agent: True):
        with Patch(agent_state, capture_pane=lambda session, agent, lines=80: "Running tool earlier\n? for shortcuts\n> "):
            with Patch(agent_state, _status_table_state=lambda agent: None):
                with Patch(agent_state, _pane_pid=lambda session, agent: 100):
                    with Patch(agent_state, _subtree_has_process=lambda pid, process_name: True):
                        with Patch(agent_state, pane_diff_classify=lambda session, agent: False):
                            state = classify("coder", "sess")
    assert state.code == "idle"
    assert state.idle_hint is True


def test_classify_special_permission_skips_pane_diff():
    def fail_diff(session, agent):
        raise AssertionError("pane diff should not run for permission prompt")

    with Patch(agent_state, _window_exists=lambda session, agent: True):
        with Patch(agent_state, capture_pane=lambda session, agent, lines=80: "Do you want to proceed?\n❯ 1. Yes"):
            with Patch(agent_state, _status_table_state=lambda agent: None):
                with Patch(agent_state, _pane_pid=lambda session, agent: 100):
                    with Patch(agent_state, _subtree_has_process=lambda pid, process_name: True):
                        with Patch(agent_state, pane_diff_classify=fail_diff):
                            state = classify("coder", "sess")
    assert state.code == "permission"


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
    print(f"\nagent_state tests: {passed}/{passed + failed} passed")
    return 0 if failed == 0 else 1


if __name__ == "__main__":
    raise SystemExit(main())
