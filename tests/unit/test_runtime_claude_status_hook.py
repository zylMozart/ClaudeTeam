"""Tests for runtime/claude_status_hook.py — STATUS_HOOK_SCRIPT + HODK_EVENTS.

New module from commit a7bf910.
"""
from __future__ import annotations

import ast

from claudeteam.runtime.claude_status_hook import HODK_EVENTS, STATUS_HOOK_SCRIPT


# ── STATUS_HOOK_SCRIPT ────────────────────────────────────────────────


def test_status_hook_script_is_valid_python():
    try:
        ast.parse(STATUS_HOOK_SCRIPT)
    except SyntaxError as e:
        raise AssertionError(f"STATUS_HOOK_SCRIPT has a syntax error: {e}") from e


def test_status_hook_script_reads_correct_agent_env_var():
    """Regression: early draft used CLAUDETEAN_AGENT_NAME (missing M).
    The hook must read CLAUDETEAM_AGENT_NAME to match what pane_env_prefix injects."""
    assert "CLAUDETEAM_AGENT_NAME" in STATUS_HOOK_SCRIPT, (
        "hook reads wrong env var — check for CLAUDETEAN_ typo"
    )
    assert "CLAUDETEAN_AGENT_NAME" not in STATUS_HOOK_SCRIPT, (
        "CLAUDETEAN_ typo still present in STATUS_HOOK_SCRIPT"
    )


def test_status_hook_script_reads_correct_state_dir_env_var():
    """Same typo risk for CLAUDETEAM_STATE_DIR."""
    assert "CLAUDETEAM_STATE_DIR" in STATUS_HOOK_SCRIPT, (
        "hook reads wrong env var — check for CLAUDETEAN_ typo"
    )
    assert "CLAUDETEAN_STATE_DIR" not in STATUS_HOOK_SCRIPT, (
        "CLAUDETEAN_ typo still present in STATUS_HOOK_SCRIPT"
    )


def test_status_hook_script_calls_claudeteam_status():
    """The hook must invoke `claudeteam status` to update agent state."""
    assert "claudeteam" in STATUS_HOOK_SCRIPT
    assert "status" in STATUS_HOOK_SCRIPT


# ── HODK_EVENTS ───────────────────────────────────────────────────────


def test_hook_events_has_four_entries():
    assert len(HODK_EVENTS) == 4


def test_hook_events_covers_expected_lifecycle_states():
    event_names = {e[0] for e in HODK_EVENTS}
    assert "UserPromptSubmit" in event_names
    assert "Stop" in event_names
    assert "StopFailure" in event_names
    assert "SessionEnd" in event_names


def test_hook_events_each_entry_is_three_tuple():
    for entry in HODK_EVENTS:
        assert len(entry) == 3, f"expected 3-tuple, got {entry!r}"
        assert all(isinstance(s, str) for s in entry), \
            f"all fields must be str, got {entry!r}"


def test_hook_events_status_values_are_nonempty():
    for event, status, task in HODK_EVENTS:
        assert status, f"empty status for event {event!r}"
        assert task, f"empty task for event {event!r}"
