"""Tests for feishu/pane_state.py — pane buffer → (emoji, brief) classifier."""
from __future__ import annotations

from claudeteam.feishu import pane_state


def test_empty_buffer_returns_no_window():
    assert pane_state.parse("") == ("⬜", "no window")


def test_bash_prompt_means_cli_dead():
    buf = "some output\nroot@abc123:/work#"
    emoji, brief = pane_state.parse(buf)
    assert emoji == "🛑"
    assert "CLI not running" in brief


def test_quota_exceeded():
    buf = "ahem\nyou hit your limit\nfoo"
    assert pane_state.parse(buf) == ("⛔", "quota exceeded")


def test_awaiting_permission():
    buf = "Do you want to proceed?\n❯ 1. yes\n❯ 2. no"
    emoji, brief = pane_state.parse(buf)
    assert emoji == "⚠️"


def test_compacting():
    assert pane_state.parse("Compacting conversation…")[0] == "🗜️"


def test_working_with_elapsed_time():
    buf = "thinking…\nesc to interrupt (1m 12s · ↓ 99 tokens)"
    emoji, brief = pane_state.parse(buf)
    assert emoji == "🔄"
    assert "1m" in brief and "12s" in brief


def test_working_no_elapsed_time():
    buf = "Manifesting solution…"
    assert pane_state.parse(buf)[0] == "🔄"


def test_idle_via_bypass_marker():
    buf = "stuff\n⏵⏵ bypass permissions on (shift+tab to cycle)"
    assert pane_state.parse(buf) == ("💤", "idle")


# ── new in round B: codex / kimi idle classification ──────────────


def test_codex_idle_via_default_status_line():
    """Round A2 B4: codex banner ends with 'gpt-5.5 default · ~/path' which
    pane_state was missing → defaulted to 🔘. Now classifies as 💤."""
    buf = (
        "╭─────────────────────────────╮\n"
        "│ >_ OpenAI Codex (v0.128.0)  │\n"
        "│ permissions: YOLO            │\n"
        "╰─────────────────────────────╯\n"
        "  Tip: Try the Codex App.\n"
        "\n"
        "  gpt-5.5 default · ~/Documents/projects/ClaudeTeam"
    )
    assert pane_state.parse(buf) == ("💤", "idle")


def test_codex_idle_via_yolo_marker_alone():
    """If only the permissions: YOLO line is visible, also idle."""
    buf = "permissions: YOLO\n"
    assert pane_state.parse(buf) == ("💤", "idle")


def test_kimi_idle_via_context_line():
    """Round A2 B4: kimi shows 'context: 0.0% (0/262.1k)' when waiting at
    prompt. Was 🔘; now 💤."""
    buf = "Welcome to Kimi Code CLI\nSend /help for help information\ncontext: 0.0% (0/262.1k)"
    assert pane_state.parse(buf) == ("💤", "idle")


def test_kimi_idle_via_input_marker():
    buf = "── input ─────"
    assert pane_state.parse(buf) == ("💤", "idle")


# ── fallback ──────────────────────────────────────────────────────


def test_unknown_buffer_falls_back_to_circle_with_tail():
    buf = "first\n\nrandom unmatched text\n"
    emoji, brief = pane_state.parse(buf)
    assert emoji == "🔘"
    assert "random unmatched text" in brief


def test_fallback_truncates_long_tails():
    long_line = "x" * 200
    emoji, brief = pane_state.parse(long_line)
    assert emoji == "🔘"
    assert len(brief) <= 40
