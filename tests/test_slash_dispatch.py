#!/usr/bin/env python3
"""Unit tests for src/claudeteam/commands/slash dispatch routing.

Tests verify that each of the 6 core commands is recognized (matched=True)
and that unrecognized input is not matched.  No live I/O — all deps injected
via SlashContext with stub callables.
"""
from __future__ import annotations

import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
for p in (ROOT / "src", ROOT / "scripts"):
    if str(p) not in sys.path:
        sys.path.insert(0, str(p))

from claudeteam.commands.slash import SlashContext, dispatch
from claudeteam.commands.slash.dispatch import is_slash_command
from claudeteam.commands.slash import standalone

# ── shared stub context ───────────────────────────────────────────────────────

def _make_ctx(**kw) -> SlashContext:
    defaults = dict(
        team_agents=["manager", "devops", "coder"],
        tmux_session="TestSession",
        capture_pane=lambda agent: f"fake pane for {agent}",
        send_to_agent=lambda session, agent, msg: True,
        query_usage=lambda tool: [f"  Claude 5.x : 42% (重置: 2h0m)"],
    )
    defaults.update(kw)
    return SlashContext(**defaults)

CTX = _make_ctx()

# ── /help ─────────────────────────────────────────────────────────────────────

def test_help_matched():
    matched, reply = dispatch("/help", CTX)
    assert matched is True, "expected matched=True for /help"
    assert reply and "ClaudeTeam" in reply


def test_help_with_whitespace():
    matched, reply = dispatch("/help  ", CTX)
    assert matched is True


def test_help_not_matched_for_wrong_cmd():
    matched, _ = dispatch("/helpme", CTX)
    assert matched is False

# ── /team ─────────────────────────────────────────────────────────────────────

def test_team_matched():
    matched, reply = dispatch("/team", CTX)
    assert matched is True
    assert reply is not None


def test_team_returns_card():
    matched, reply = dispatch("/team", CTX)
    assert matched is True
    assert isinstance(reply, dict) and "card" in reply


def test_team_not_matched_for_wrong_cmd():
    matched, _ = dispatch("/teamwork", CTX)
    assert matched is False

# ── /usage ────────────────────────────────────────────────────────────────────

def test_usage_matched():
    matched, reply = dispatch("/usage", CTX)
    assert matched is True
    assert reply is not None


def test_usage_returns_card():
    matched, reply = dispatch("/usage", CTX)
    assert matched is True
    assert isinstance(reply, dict) and "card" in reply


def test_usage_not_matched_for_wrong_cmd():
    matched, _ = dispatch("/usages", CTX)
    assert matched is False

# ── /tmux ─────────────────────────────────────────────────────────────────────

def test_tmux_matched_bare():
    matched, reply = dispatch("/tmux", CTX)
    assert matched is True
    assert reply is not None


def test_tmux_matched_with_agent():
    matched, reply = dispatch("/tmux devops", CTX)
    assert matched is True
    assert "devops" in reply


def test_tmux_matched_with_agent_and_lines():
    matched, reply = dispatch("/tmux devops 50", CTX)
    assert matched is True
    assert "50" in reply


def test_tmux_unknown_agent():
    matched, reply = dispatch("/tmux unknown_agent", CTX)
    assert matched is True
    assert "未知" in reply or "unknown" in reply.lower()


def test_tmux_not_matched_wrong_pattern():
    matched, _ = dispatch("/tmuxed", CTX)
    assert matched is False

# ── /send ─────────────────────────────────────────────────────────────────────

def test_send_matched():
    matched, reply = dispatch("/send manager 你好", CTX)
    assert matched is True
    assert reply is not None


def test_send_no_message_shows_usage():
    matched, reply = dispatch("/send manager", CTX)
    assert matched is True
    assert "用法" in reply


def test_send_bare_shows_usage():
    matched, reply = dispatch("/send", CTX)
    assert matched is True
    assert "用法" in reply


def test_send_unknown_agent():
    matched, reply = dispatch("/send nobody hello", CTX)
    assert matched is True
    assert "未知" in reply or "白名单" in reply


def test_send_not_matched_wrong_prefix():
    matched, _ = dispatch("/sender x y", CTX)
    assert matched is False


def test_is_slash_command_has_no_send_side_effect():
    calls = []
    ctx = _make_ctx(send_to_agent=lambda session, agent, msg: calls.append((session, agent, msg)) or True)
    assert is_slash_command("/send manager hello") is True
    assert calls == []
    matched, _ = dispatch("/send manager hello", ctx)
    assert matched is True
    assert calls == [("TestSession", "manager", "hello")]

# ── /compact ─────────────────────────────────────────────────────────────────

def test_compact_matched_bare():
    matched, reply = dispatch("/compact", CTX)
    assert matched is True
    assert reply is not None


def test_compact_matched_with_agent():
    matched, reply = dispatch("/compact devops", CTX)
    assert matched is True
    assert "devops" in reply


def test_compact_unknown_agent():
    matched, reply = dispatch("/compact nobody", CTX)
    assert matched is True
    assert "未知" in reply


# ── unmatched inputs ──────────────────────────────────────────────────────────

def test_empty_string_not_matched():
    matched, _ = dispatch("", CTX)
    assert matched is False


def test_plain_text_not_matched():
    matched, _ = dispatch("hello world", CTX)
    assert matched is False


def test_unknown_slash_not_matched():
    matched, _ = dispatch("/unknowncmd", CTX)
    assert matched is False


def test_standalone_send_clears_residual_input_before_retry():
    calls = []

    class Result:
        def __init__(self, ok, unsafe_input=False):
            self.ok = ok
            self.submitted = ok
            self.unsafe_input = unsafe_input
            self.error = "unsafe unsubmitted input" if unsafe_input else ""

        def __bool__(self):
            return self.ok

    results = [Result(False, unsafe_input=True), Result(True)]

    def fake_inject(session, agent, msg, wait_secs=5, force_after_wait=True):
        calls.append(("inject", session, agent, msg))
        return results.pop(0)

    def fake_run(cmd, **kwargs):
        calls.append(("run", cmd))
        class R:
            returncode = 0
        return R()

    original_import = __import__
    original_run = standalone.subprocess.run
    try:
        def fake_import(name, globals=None, locals=None, fromlist=(), level=0):
            if name == "claudeteam.runtime.tmux_utils" and "inject_when_idle" in fromlist:
                class M:
                    inject_when_idle = staticmethod(fake_inject)
                return M
            return original_import(name, globals, locals, fromlist, level)
        import builtins
        builtins.__import__ = fake_import
        standalone.subprocess.run = fake_run
        assert standalone._send_to_agent("S", "devops", "hello") is True
    finally:
        import builtins
        builtins.__import__ = original_import
        standalone.subprocess.run = original_run
    assert ("run", ["tmux", "send-keys", "-t", "S:devops", "C-u"]) in calls
    assert [c[0] for c in calls].count("inject") == 2


def test_standalone_dispatch_does_not_fallback_to_scripts(monkeypatch=None):
    original_build_context = standalone.build_context
    original_src_dispatch = standalone._src_dispatch
    try:
        standalone.build_context = lambda: CTX
        standalone._src_dispatch = lambda text, ctx: (True, "src-only") if text == "/help" else (False, None)
        assert not hasattr(standalone, "_try_scripts_slash")
        assert standalone.dispatch("/help") == (True, "src-only")
    finally:
        standalone.build_context = original_build_context
        standalone._src_dispatch = original_src_dispatch


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
    print(f"\nslash dispatch tests: {passed}/{passed + failed} passed")
    return 0 if failed == 0 else 1


if __name__ == "__main__":
    raise SystemExit(main())
