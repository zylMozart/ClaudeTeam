"""Tests for the top-level claudeteam CLI dispatcher."""
from __future__ import annotations

from helpers import run_cli
from claudeteam import cli


def test_no_args_prints_usage_and_returns_zero():
    rc, out, _ = run_cli([])
    assert rc == 0
    assert "usage: claudeteam" in out


def test_help_prints_usage():
    rc, out, _ = run_cli(["--help"])
    assert rc == 0
    assert "commands:" in out


def test_help_groups_commands_by_category():
    """Round-93: usage output renders commands grouped by `[group label]`
    section instead of a flat alphabetical wall. New operators see
    related commands together (`[team lifecycle]` has start/up/down,
    `[durable agent memory]` has remember/recall, etc.)."""
    rc, out, _ = run_cli(["--help"])
    assert rc == 0
    # At least the four most-used groups must appear as section labels
    assert "[bootstrap]" in out
    assert "[team lifecycle]" in out
    assert "[feishu transport]" in out
    assert "[durable agent memory]" in out
    # Commands appear under their group, indented
    # (memory commands sit together)
    rem = out.index("remember")
    rec = out.index("recall")
    mem_label = out.index("[durable agent memory]")
    # Both commands appear AFTER the group label
    assert mem_label < rem
    assert mem_label < rec
    # And before the next group
    op_label = out.index("[operational]")
    assert rem < op_label
    assert rec < op_label


def test_command_groups_and_flat_dict_in_sync():
    """The flat COMMANDS dict is built from _COMMAND_GROUPS so a command
    can never exist in one but not the other. Pin that invariant —
    catches a future contributor adding to one and forgetting the other."""
    from_groups = {
        name for _, pairs in cli._COMMAND_GROUPS for name, _ in pairs
    }
    assert from_groups == set(cli.COMMANDS)


def test_unknown_command_returns_one_and_writes_to_stderr():
    rc, _, err = run_cli(["__definitely_unknown__"])
    assert rc == 1
    assert "unknown command" in err


def test_registered_handler_runs_and_propagates_exit_code():
    captured = []

    def handler(argv: list[str]) -> int:
        captured.append(argv)
        return 7

    cli.COMMANDS["echo"] = handler
    try:
        rc, _, _ = run_cli(["echo", "a", "b"])
    finally:
        del cli.COMMANDS["echo"]
    assert rc == 7
    assert captured == [["a", "b"]]


def test_handler_returning_none_is_treated_as_zero():
    cli.COMMANDS["noop"] = lambda argv: None
    try:
        rc, _, _ = run_cli(["noop"])
    finally:
        del cli.COMMANDS["noop"]
    assert rc == 0


def test_handler_keyboard_interrupt_returns_130_without_traceback():
    """Ctrl-C should produce exit 130 (standard SIGINT) and a clean
    newline to stderr, NOT a Python KeyboardInterrupt traceback."""
    def handler(argv):
        raise KeyboardInterrupt()

    cli.COMMANDS["sigint"] = handler
    try:
        rc, _, err = run_cli(["sigint"])
    finally:
        del cli.COMMANDS["sigint"]
    assert rc == 130
    # No "Traceback" or "KeyboardInterrupt" leaked to stderr
    assert "Traceback" not in err
    assert "KeyboardInterrupt" not in err


def test_handler_unhandled_exception_prints_friendly_error():
    """A bug in a handler should produce a one-liner, not a 30-line
    Python traceback. Set CLAUDETEAM_DEBUG=1 to see the trace."""
    def handler(argv):
        raise RuntimeError("something exploded")

    cli.COMMANDS["boom"] = handler
    try:
        rc, _, err = run_cli(["boom"])
    finally:
        del cli.COMMANDS["boom"]
    assert rc == 1
    assert "boom: unhandled error: RuntimeError: something exploded" in err
    assert "CLAUDETEAM_DEBUG=1" in err
    # No traceback by default
    assert "Traceback" not in err


def test_handler_unhandled_exception_with_debug_env_reraises():
    """When CLAUDETEAM_DEBUG=1 is set, the original exception propagates
    so devs can see the full trace + stack frames."""
    from helpers import env_patch

    def handler(argv):
        raise RuntimeError("debug me")

    cli.COMMANDS["debug-boom"] = handler
    try:
        with env_patch(CLAUDETEAM_DEBUG="1"):
            try:
                run_cli(["debug-boom"])
            except RuntimeError as e:
                assert "debug me" in str(e)
            else:
                raise AssertionError("expected RuntimeError to propagate")
    finally:
        del cli.COMMANDS["debug-boom"]
