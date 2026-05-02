"""Tests for the top-level claudeteam CLI dispatcher."""
from __future__ import annotations

import io
import contextlib

from claudeteam import cli


def test_no_args_prints_usage_and_returns_zero(capsys=None):
    out = io.StringIO()
    with contextlib.redirect_stdout(out):
        rc = cli.main([])
    assert rc == 0
    assert "usage: claudeteam" in out.getvalue()


def test_help_prints_usage():
    out = io.StringIO()
    with contextlib.redirect_stdout(out):
        rc = cli.main(["--help"])
    assert rc == 0
    assert "commands:" in out.getvalue()


def test_unknown_command_returns_one_and_writes_to_stderr():
    out, err = io.StringIO(), io.StringIO()
    with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
        rc = cli.main(["__definitely_unknown__"])
    assert rc == 1
    assert "unknown command" in err.getvalue()


def test_registered_handler_runs_and_propagates_exit_code():
    captured = []

    def handler(argv: list[str]) -> int:
        captured.append(argv)
        return 7

    cli.COMMANDS["echo"] = handler
    try:
        rc = cli.main(["echo", "a", "b"])
    finally:
        del cli.COMMANDS["echo"]
    assert rc == 7
    assert captured == [["a", "b"]]


def test_handler_returning_none_is_treated_as_zero():
    cli.COMMANDS["noop"] = lambda argv: None
    try:
        rc = cli.main(["noop"])
    finally:
        del cli.COMMANDS["noop"]
    assert rc == 0
