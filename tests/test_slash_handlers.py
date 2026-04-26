#!/usr/bin/env python3
"""Unit tests for individual slash handler logic.

Tests pure parsing/formatting logic independent of dispatch routing.
No live I/O — stubs only.
"""
from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
for p in (ROOT / "src", ROOT / "scripts"):
    if str(p) not in sys.path:
        sys.path.insert(0, str(p))

from claudeteam.commands.slash.context import SlashContext
from claudeteam.commands.slash import help_
from claudeteam.commands.slash.team import parse_agent_state, handle_team, handle_stop, handle_clear
from claudeteam.commands.slash.tmux_ import handle_tmux, handle_send, handle_compact
from claudeteam.commands.slash.usage import parse_usage_lines, build_usage_card, handle_usage


def _ctx(**kw) -> SlashContext:
    defaults = dict(
        team_agents=["manager", "devops"],
        tmux_session="S",
        capture_pane=lambda a: f"last line >\n",
        send_to_agent=lambda s, a, m: True,
        query_usage=lambda t: ["  Claude 5.x : 55% (重置: 1h30m)"],
    )
    defaults.update(kw)
    return SlashContext(**defaults)


# ── help_.handle ─────────────────────────────────────────────────────────────

def test_help_returns_text():
    r = help_.handle("/help", None)
    assert r is not None and "/usage" in r


def test_help_no_match():
    assert help_.handle("/helper", None) is None
    assert help_.handle("help", None) is None

# ── parse_agent_state ─────────────────────────────────────────────────────────

def test_state_empty_pane():
    assert parse_agent_state("") == ("❔", "无窗口")


def test_state_bash_prompt():
    buf = "root@abc123:/app# "
    emoji, label = parse_agent_state(buf)
    assert emoji == "🛑"
    assert "bash" in label or "未运行" in label


def test_state_spinner():
    buf = "doing work ⣾"
    emoji, label = parse_agent_state(buf)
    assert emoji == "⚡"


def test_state_thinking():
    buf = "Thinking…"
    emoji, label = parse_agent_state(buf)
    assert emoji == "⚡"


def test_state_idle_prompt():
    buf = "some output\n> "
    emoji, label = parse_agent_state(buf)
    assert emoji == "✅"


def test_state_limit_hit():
    buf = "you have hit your limit for this week"
    emoji, _ = parse_agent_state(buf)
    assert emoji == "🔴"

# ── handle_team ───────────────────────────────────────────────────────────────

def test_team_contains_all_agents():
    ctx = _ctx()
    result = handle_team("/team", ctx)
    assert result is not None
    assert "manager" in result["text"]
    assert "devops" in result["text"]


def test_team_wrong_cmd_returns_none():
    assert handle_team("/teams", _ctx()) is None

# ── handle_stop ───────────────────────────────────────────────────────────────

def test_stop_known_agent():
    ctx = _ctx()
    r = handle_stop("/stop devops", ctx)
    assert r is not None and "devops" in r


def test_stop_unknown_agent():
    r = handle_stop("/stop ghost", _ctx())
    assert "未知" in r


def test_stop_bare_shows_usage():
    r = handle_stop("/stop", _ctx())
    assert "用法" in r


def test_stop_wrong_cmd_returns_none():
    assert handle_stop("/stopping devops", _ctx()) is None

# ── handle_clear ─────────────────────────────────────────────────────────────

def test_clear_known_agent():
    r = handle_clear("/clear manager", _ctx())
    assert r is not None and "manager" in r


def test_clear_unknown_agent():
    r = handle_clear("/clear ghost", _ctx())
    assert "未知" in r

# ── handle_tmux ──────────────────────────────────────────────────────────────

def test_tmux_default_agent():
    ctx = _ctx()
    r = handle_tmux("/tmux", ctx)
    assert r is not None and "manager" in r  # first agent


def test_tmux_specific_agent_and_lines():
    ctx = _ctx()
    r = handle_tmux("/tmux devops 30", ctx)
    assert "devops" in r and "30" in r


def test_tmux_clamps_lines():
    ctx = _ctx()
    r = handle_tmux("/tmux devops 99999", ctx)
    assert r is not None  # clamped but returns


def test_tmux_unknown_agent():
    r = handle_tmux("/tmux ghost", _ctx())
    assert "未知" in r

# ── handle_send ──────────────────────────────────────────────────────────────

def test_send_success():
    ctx = _ctx()
    r = handle_send("/send devops 你好", ctx)
    assert "✅" in r and "devops" in r and "你好" in r


def test_send_fail():
    ctx = _ctx(send_to_agent=lambda s, a, m: False)
    r = handle_send("/send devops 你好", ctx)
    assert "❌" in r


def test_send_missing_message():
    r = handle_send("/send devops", _ctx())
    assert "用法" in r


def test_send_invalid_agent_name():
    r = handle_send("/send bad!name hello", _ctx())
    assert "非法" in r


def test_send_no_agent_no_message():
    r = handle_send("/send", _ctx())
    assert "用法" in r

# ── handle_compact ───────────────────────────────────────────────────────────

def test_compact_default():
    r = handle_compact("/compact", _ctx())
    assert r is not None and "manager" in r


def test_compact_named_agent():
    r = handle_compact("/compact devops", _ctx())
    assert "devops" in r


def test_compact_unknown():
    r = handle_compact("/compact ghost", _ctx())
    assert "未知" in r

# ── parse_usage_lines ────────────────────────────────────────────────────────

def test_parse_quota_line():
    lines = ["  Claude 5.x : 42% (重置: 2h30m)"]
    items = parse_usage_lines(lines)
    assert len(items) == 1
    assert items[0]["type"] == "quota"
    assert items[0]["pct"] == 42.0


def test_parse_extra_line():
    lines = ["  Extra usage : $5.50 / $25.00 (22%) [USD]"]
    items = parse_usage_lines(lines)
    assert len(items) == 1
    assert items[0]["type"] == "extra"
    assert items[0]["used"] == 5.5


def test_parse_empty_returns_empty():
    assert parse_usage_lines([]) == []


def test_parse_unrecognized_skipped():
    lines = ["some random line", "  Claude 5.x : 10% (重置: 1h)"]
    items = parse_usage_lines(lines)
    assert len(items) == 1

# ── handle_usage ─────────────────────────────────────────────────────────────

def test_usage_matched():
    r = handle_usage("/usage", _ctx())
    assert r is not None
    assert isinstance(r, dict) and "card" in r


def test_usage_wrong_cmd():
    assert handle_usage("/usages", _ctx()) is None


# ── runner ────────────────────────────────────────────────────────────────────

def main() -> int:
    tests = [v for k, v in sorted(globals().items()) if k.startswith("test_")]
    passed = failed = 0
    for fn in tests:
        try:
            fn()
            print(f"  ✅ {fn.__name__}")
            passed += 1
        except Exception as e:
            print(f"  ❌ {fn.__name__}: {e}")
            failed += 1
    print(f"\nslash handlers tests: {passed}/{passed + failed} passed")
    return 0 if failed == 0 else 1


if __name__ == "__main__":
    raise SystemExit(main())
