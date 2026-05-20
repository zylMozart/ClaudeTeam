"""Tests for runtime/claude_statusline.py — STATUSLINE_SCRIPT.

New module from commit a7bf910.
"""
from __future__ import annotations

import ast

from claudeteam.runtime.claude_statusline import STATUSLINE_SCRIPT


# ── syntax + correctness ──────────────────────────────────────────────


def test_statusline_script_is_valid_python():
    try:
        ast.parse(STATUSLINE_SCRIPT)
    except SyntaxError as e:
        raise AssertionError(f"STATUSLINE_SCRIPT has a syntax error: {e}") from e


def test_statusline_script_reset_code_is_digit_zero():
    """Regression: early draft used '\\033[om' (letter o) instead of
    '\\033[0m' (digit 0). The letter form is not a valid ANSI escape and
    leaves garbage in the terminal output."""
    assert r"\033[0m" in STATUSLINE_SCRIPT or "\033[0m" in STATUSLINE_SCRIPT, (
        "RESET escape must use digit 0 (\\033[0m), not letter o (\\033[om)"
    )
    assert "\033[om" not in STATUSLINE_SCRIPT and r"\033[om" not in STATUSLINE_SCRIPT, (
        "RESET = '\\033[om' typo still present — must be '\\033[0m'"
    )


def test_statusline_script_join_is_method_call():
    """Regression: early draft had `print(' | ', join(parts))` which calls
    a bare `join` builtin (doesn't exist) instead of `' | '.join(parts)`.
    Check the correct form is present."""
    assert '" | ".join(parts)' in STATUSLINE_SCRIPT or "' | '.join(parts)" in STATUSLINE_SCRIPT, (
        "statusline must use ' | '.join(parts), not print(' | ', join(parts))"
    )


def test_statusline_script_reads_context_window():
    """Script must extract context_window data to render the progress bar."""
    assert "context_window" in STATUSLINE_SCRIPT


def test_statusline_script_renders_progress_bar():
    """Bar characters must be present so the filled/empty bar renders."""
    assert "█" in STATUSLINE_SCRIPT
    assert "░" in STATUSLINE_SCRIPT


def test_statusline_script_has_color_thresholds():
    """Color thresholds (70 / 90 %) must be present for the bar coloring logic."""
    assert "70" in STATUSLINE_SCRIPT
    assert "90" in STATUSLINE_SCRIPT


def test_statusline_script_calls_git_branch():
    """Script must attempt to read the git branch for display."""
    assert "git_branch" in STATUSLINE_SCRIPT
    assert "branch" in STATUSLINE_SCRIPT


def test_statusline_script_fmt_tokens_handles_millions():
    """fmt_tokens must format values >= 1_000_000 with M suffix."""
    # Extract and exec just the fmt_tokens function from the script
    tree = ast.parse(STATUSLINE_SCRIPT)
    fn_src = None
    for node in ast.walk(tree):
        if isinstance(node, ast.FunctionDef) and node.name == "fmt_tokens":
            fn_src = ast.get_source_segment(STATUSLINE_SCRIPT, node)
            break
    assert fn_src is not None, "fmt_tokens function not found in STATUSLINE_SCRIPT"
    ns: dict = {}
    exec(compile(fn_src, "<fmt_tokens>", "exec"), ns)
    fmt = ns["fmt_tokens"]
    assert fmt(1_500_000).endswith("M")
    assert fmt(2_000).endswith("K")
    assert fmt(500) == "500"
