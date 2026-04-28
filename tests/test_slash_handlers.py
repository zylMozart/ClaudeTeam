#!/usr/bin/env python3
"""Unit tests for individual slash handler logic.

Tests pure parsing/formatting logic independent of dispatch routing.
No live I/O — stubs only.
"""
from __future__ import annotations

import os
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
for p in (ROOT / "src", ROOT / "scripts"):
    if str(p) not in sys.path:
        sys.path.insert(0, str(p))

from claudeteam.commands.slash.context import SlashContext
from claudeteam.commands.slash import help_
from claudeteam.commands.slash.team import parse_agent_state, build_team_response, handle_team
from claudeteam.commands.slash.tmux_ import (
    clear_command,
    compact_command,
    handle_clear,
    handle_compact,
    handle_send,
    handle_stop,
    handle_tmux,
    send_command,
    stop_command,
    tmux_command,
)
from claudeteam.commands.slash import usage
from claudeteam.commands.slash.usage import parse_usage_lines, build_usage_card, handle_usage, usage_command


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
    assert parse_agent_state("") == ("⬜", "无窗口")


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


def test_team_reads_models_from_team_json():
    with tempfile.TemporaryDirectory() as td:
        root = Path(td)
        (root / "team.json").write_text('{"agents":{"manager":{"model":"claude-opus-4-6"},"devops":{"model":"gpt-5.5"}}}', encoding="utf-8")
        result = handle_team("/team", _ctx(project_root=root))
        assert "claude-opus-4-6" in result["text"]
        assert "gpt-5.5" in result["text"]
        card_text = str(result["card"])
        assert "claude-opus-4-6" in card_text and "gpt-5.5" in card_text


def test_team_wrong_cmd_returns_none():
    assert handle_team("/teams", _ctx()) is None


def test_team_full_response_includes_host_and_container_windows():
    class R:
        def __init__(self, returncode=0, stdout=""):
            self.returncode = returncode
            self.stdout = stdout
            self.stderr = ""

    calls = []

    def run_fn(cmd, timeout=5):
        calls.append(cmd)
        if cmd[:3] == ["tmux", "list-windows", "-t"]:
            return R(stdout="manager\n")
        if cmd == ["tmux", "capture-pane", "-t", "S:manager", "-p"]:
            return R(stdout="host pane")
        if cmd[:5] == ["sudo", "-n", "docker", "ps", "--format"]:
            return R(stdout="claudeteam-alpha-team-1\n")
        if cmd[:6] == ["sudo", "-n", "docker", "exec", "claudeteam-alpha-team-1", "tmux"] and "list-windows" in cmd:
            return R(stdout="C:devops\n")
        if cmd[:6] == ["sudo", "-n", "docker", "exec", "claudeteam-alpha-team-1", "tmux"] and "capture-pane" in cmd:
            return R(stdout="root@abc123:/app# ")
        return R(returncode=1)

    class State:
        code = "idle"
        emoji = "💤"
        brief = "idle"

    result = build_team_response(["manager", "devops"], "S", "09:30", run_fn=run_fn, classify_fn=lambda a, s: State())
    assert "[本机 S]" in result["text"]
    assert "manager" in result["text"] and "idle" in result["text"]
    assert "[容器 alpha]" in result["text"]
    assert "devops" in result["text"] and "bash" in result["text"]
    assert result["card"]["elements"][0]["text"]["content"] == "**本机 S**"
    assert any("capture-pane" in c for cmd in calls for c in cmd)


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


def _live_run(commands):
    class R:
        def __init__(self, returncode=0, stdout="", stderr=""):
            self.returncode = returncode
            self.stdout = stdout
            self.stderr = stderr

    calls = []

    def run_fn(cmd, timeout=5):
        calls.append(cmd)
        key = tuple(cmd)
        value = commands.get(key)
        if value is None:
            return R(returncode=0)
        if isinstance(value, R):
            return value
        return R(stdout=value)

    return run_fn, calls, R


def test_tmux_command_reads_container_target():
    run_fn, calls, _ = _live_run({
        ("tmux", "list-windows", "-t", "S", "-F", "#{window_name}"): "manager\n",
        ("sudo", "-n", "docker", "ps", "--format", "{{.Names}}"): "claudeteam-alpha-team-1\n",
        ("sudo", "-n", "docker", "exec", "claudeteam-alpha-team-1", "tmux", "list-windows", "-a", "-F", "#{session_name}:#{window_name}"): "C:devops\n",
        ("sudo", "-n", "docker", "exec", "claudeteam-alpha-team-1", "tmux", "capture-pane", "-t", "C:devops", "-p", "-S", "-20"): "container pane\n",
    })
    r = tmux_command("/tmux devops 20", ["manager", "devops"], "S", frozenset({"manager", "devops"}), run_fn)
    assert "claudeteam-alpha-team-1 C:devops" in r
    assert "container pane" in r
    assert any(cmd[:5] == ["sudo", "-n", "docker", "exec", "claudeteam-alpha-team-1"] for cmd in calls)


def test_send_command_uses_container_literal_send():
    run_fn, calls, _ = _live_run({
        ("tmux", "list-windows", "-t", "S", "-F", "#{window_name}"): "manager\n",
        ("sudo", "-n", "docker", "ps", "--format", "{{.Names}}"): "claudeteam-alpha-team-1\n",
        ("sudo", "-n", "docker", "exec", "claudeteam-alpha-team-1", "tmux", "list-windows", "-a", "-F", "#{session_name}:#{window_name}"): "C:devops\n",
    })
    r = send_command("/send devops hello", ["manager", "devops"], "S", frozenset({"manager", "devops"}), run_fn, lambda _: None)
    assert "/send → claudeteam-alpha-team-1 C:devops" in r
    assert ["sudo", "-n", "docker", "exec", "claudeteam-alpha-team-1", "tmux", "send-keys", "-l", "-t", "C:devops", "hello"] in calls


def test_compact_command_resolves_host_target():
    run_fn, calls, _ = _live_run({
        ("tmux", "list-windows", "-t", "S", "-F", "#{window_name}"): "manager\n",
    })
    r = compact_command("/compact manager", ["manager"], "S", frozenset({"manager"}), run_fn, lambda _: None)
    assert "/compact → S:manager" in r
    assert ["tmux", "send-keys", "-l", "-t", "S:manager", "/compact"] in calls


def test_stop_command_sends_ctrl_c_not_text():
    run_fn, calls, _ = _live_run({
        ("tmux", "list-windows", "-t", "S", "-F", "#{window_name}"): "manager\n",
    })
    r = stop_command("/stop manager", ["manager"], "S", frozenset({"manager"}), run_fn)
    assert "C-c 已送" in r
    assert ["tmux", "send-keys", "-t", "S:manager", "C-c"] in calls
    assert not any("-l" in cmd and "C-c" in cmd for cmd in calls)


def test_clear_command_sends_rehire_init_msg_to_container():
    run_fn, calls, _ = _live_run({
        ("tmux", "list-windows", "-t", "S", "-F", "#{window_name}"): "manager\n",
        ("sudo", "-n", "docker", "ps", "--format", "{{.Names}}"): "claudeteam-alpha-team-1\n",
        ("sudo", "-n", "docker", "exec", "claudeteam-alpha-team-1", "tmux", "list-windows", "-a", "-F", "#{session_name}:#{window_name}"): "C:devops\n",
    })
    r = clear_command("/clear devops", ["manager", "devops"], "S", frozenset({"manager", "devops"}), run_fn, lambda _: None)
    assert "重新入职 init_msg" in r
    sent_literals = [cmd[-1] for cmd in calls
                     if cmd[:8] == ["sudo", "-n", "docker", "exec", "claudeteam-alpha-team-1", "tmux", "send-keys", "-l"]]
    assert "/clear" in sent_literals
    assert any("agents/devops/identity.md" in msg for msg in sent_literals)


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


def test_usage_command_queries_claude_snapshot_no_live():
    with tempfile.TemporaryDirectory() as td:
        root = Path(td)

        class R:
            returncode = 0
            stdout = "Claude 5.x : 42% (重置: 2026-04-28T01:00:00Z)\nExtra usage : $5 / $25 (20%) [USD]\n"
            stderr = ""

        calls = []

        def run_fn(cmd, timeout=5, env=None):
            calls.append(cmd)
            return R()

        r = usage_command("/usage cc", project_root=root, run_fn=run_fn, inspect_fn=lambda name, respect_enabled=False: {"status": usage.STATUS_OK})
        assert "Claude Code" in r["text"]
        assert "Extra usage" in r["text"]
        assert calls == [["python3", str(root / "scripts" / "usage_snapshot.py")]]
        assert r["card"]["header"]["title"]["content"].startswith("📊 /usage")


def test_usage_command_queries_codex_cli_tool():
    class R:
        returncode = 0
        stdout = "Plan: Plus\n5h Usage 30% resets 2h\n"
        stderr = ""

    def run_fn(cmd, timeout=5, env=None):
        assert cmd == ["/bin/codex-cli-usage"]
        return R()

    r = usage_command(
        "/usage codex",
        run_fn=run_fn,
        which_fn=lambda tool: "/bin/codex-cli-usage" if tool == "codex-cli-usage" else None,
        inspect_fn=lambda name, respect_enabled=False: {"status": usage.STATUS_OK},
    )
    assert "Plan" in r["text"]
    assert "70%" in r["text"]


def test_gemini_usage_env_detects_bundled_oauth():
    with tempfile.TemporaryDirectory() as td:
        root = Path(td)
        bundle = root / "lib" / "node_modules" / "@google" / "gemini-cli" / "bundle"
        bundle.mkdir(parents=True)
        (bundle / "chunk-test.js").write_text("code_assist/oauth2 const OAUTH_CLIENT_ID = 'id'; const OAUTH_CLIENT_SECRET = 'secret';", encoding="utf-8")
        old_id = os.environ.pop("GEMINI_OAUTH_CLIENT_ID", None)
        old_secret = os.environ.pop("GEMINI_OAUTH_CLIENT_SECRET", None)
        try:
            env = usage._gemini_usage_env(lambda tool: str(root / "bin" / "gemini") if tool == "gemini" else None)
        finally:
            if old_id is not None:
                os.environ["GEMINI_OAUTH_CLIENT_ID"] = old_id
            if old_secret is not None:
                os.environ["GEMINI_OAUTH_CLIENT_SECRET"] = old_secret
        assert env["GEMINI_OAUTH_CLIENT_ID"] == "id"
        assert env["GEMINI_OAUTH_CLIENT_SECRET"] == "secret"


def test_handle_usage_bare_queries_all_providers():
    with tempfile.TemporaryDirectory() as td:
        root = Path(td)
        (root / "team.json").write_text('{"agents":{}}', encoding="utf-8")

        class R:
            def __init__(self, stdout=""):
                self.returncode = 0
                self.stdout = stdout
                self.stderr = ""

        def run_fn(cmd, timeout=5, env=None):
            if cmd[0] == "python3":
                return R("Claude 5.x : 42% (重置: 2026-04-28T01:00:00Z)\n")
            if cmd == ["/bin/codex-cli-usage"]:
                return R("Plan: pro\nSession (5h) 3% resets 4h\n")
            if cmd == ["/bin/gemini-cli-usage"]:
                return R("Auth: Google login\ngemini-2.5-pro 2% used resets 1h\n")
            return R("")

        ctx = _ctx(project_root=root, query_usage=lambda _: [], live_usage=True)
        old_run = usage._run
        old_which = usage.shutil.which
        old_api = usage._query_kimi_usage_api
        try:
            usage._run = run_fn
            usage.shutil.which = lambda tool: {"codex-cli-usage": "/bin/codex-cli-usage", "gemini-cli-usage": "/bin/gemini-cli-usage"}.get(tool)
            usage._query_kimi_usage_api = lambda project_root=None: [{"label": "Weekly limit", "pct": 10, "display_pct": 90, "detail": "剩余 90%"}]
            result = handle_usage("/usage", ctx)
        finally:
            usage._run = old_run
            usage.shutil.which = old_which
            usage._query_kimi_usage_api = old_api
        text = result["text"]
        assert "Claude Code" in text
        assert "Codex" in text and "Session" in text
        assert "Gemini" in text and "gemini-2.5-pro" in text
        assert "Kimi" in text and "Weekly limit" in text


def test_usage_command_queries_gemini_with_oauth_env():
    class R:
        def __init__(self, stdout=""):
            self.returncode = 0
            self.stdout = stdout
            self.stderr = ""

    envs = []

    def run_fn(cmd, timeout=5, env=None):
        envs.append(env)
        return R("Auth: OAuth\ngemini-2.5-pro 12.5% used resets 1h\n")

    r = usage_command(
        "/usage gemini",
        run_fn=run_fn,
        which_fn=lambda tool: "/bin/gemini-cli-usage" if tool == "gemini-cli-usage" else None,
        inspect_fn=lambda name, respect_enabled=False: {"status": usage.STATUS_OK},
    )
    assert "Gemini" in r["text"]
    assert "88%" in r["text"]
    assert envs


def test_usage_command_kimi_tmux_fallback_parses_usage():
    with tempfile.TemporaryDirectory() as td:
        root = Path(td)
        (root / "team.json").write_text('{"agents":{"kimi":{"cli":"kimi-code"}}}', encoding="utf-8")

        class R:
            def __init__(self, returncode=0, stdout=""):
                self.returncode = returncode
                self.stdout = stdout
                self.stderr = ""

        def run_fn(cmd, timeout=5, env=None):
            if cmd[:3] == ["tmux", "list-windows", "-t"]:
                return R(stdout="kimi\n")
            if cmd[:3] == ["tmux", "list-panes", "-a"]:
                return R(stdout="")
            if cmd[:3] == ["tmux", "capture-pane", "-pt"]:
                return R(stdout="Weekly limit ━━━ 99% left (resets in 4d 39m)\n")
            return R()

        old_api = usage._query_kimi_usage_api
        try:
            usage._query_kimi_usage_api = lambda project_root=None: None
            r = usage_command(
                "/usage kimi",
                project_root=root,
                session="S",
                agent_set=frozenset({"kimi"}),
                run_fn=run_fn,
                sleep_fn=lambda _: None,
                inspect_fn=lambda name, respect_enabled=False: {"status": usage.STATUS_OK},
            )
        finally:
            usage._query_kimi_usage_api = old_api
        assert "Kimi" in r["text"]
        assert "99%" in r["text"]
        assert "Weekly limit" in r["text"]


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
