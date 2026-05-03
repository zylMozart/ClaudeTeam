"""Tests for feishu/slash.py — router-level slash command dispatch."""
from __future__ import annotations

from helpers import attr_patch, tmux_patch
from claudeteam.feishu import slash
from claudeteam.runtime import tmux


def _ctx(*, agents=("manager", "worker_cc", "worker_codex"),
         session="ClaudeTeam", run=None, sleep=None, background=None):
    """Build a SlashContext for tests with sane stubs by default."""
    fake_run = run or (lambda *a, **kw: type("R", (), {
        "returncode": 0, "stdout": "ok\n", "stderr": ""})())
    fake_sleep = sleep or (lambda _s: None)
    # Default: drop background callbacks (no real thread, no eager
    # execution) so test inject capture isn't polluted by post-compact
    # reidentify firing inline.
    fake_background = background or (lambda _fn: None)
    return slash.SlashContext(
        team_agents=list(agents),
        session=session,
        run=fake_run,
        sleep=fake_sleep,
        background=fake_background,
    )


# ── is_slash_command (pure) ──────────────────────────────────────


def test_is_slash_command_recognises_known_commands():
    for ok in ("/help", "/team", "/health", "/usage", "/usage daily",
               "/tmux manager", "/tmux manager 50",
               "/send manager hello", "/compact", "/compact manager",
               "/stop manager", "/clear manager"):
        assert slash.is_slash_command(ok), f"should match: {ok}"


def test_is_slash_command_rejects_non_slash_or_unknown():
    for bad in ("", "no slash here", "/unknown", "/", "  /team  ",  # leading-ws OK
                "regular text"):
        if bad.strip().startswith("/") and any(
                bad.strip().startswith(p) for p in
                ("/help", "/team", "/health", "/usage", "/tmux",
                 "/send", "/compact", "/stop", "/clear")):
            continue
        assert not slash.is_slash_command(bad), f"should NOT match: {bad!r}"


def test_is_slash_command_handles_leading_whitespace():
    # The classifier uses `text.startswith("/")` after strip; whitespace OK.
    assert slash.is_slash_command("  /team  ") is True


# ── /help ────────────────────────────────────────────────────────


def test_help_returns_card_listing_all_commands():
    """Round-79: /help now returns a Feishu card dict (not text). The card's
    body element is the same _HELP_TEXT block, so command-name search runs
    against `elements[0]['text']['content']` instead of the bare reply.
    Round-95: /recall added — must also appear."""
    reply = slash.dispatch("/help", _ctx())
    assert isinstance(reply, dict), f"/help should return a card dict, got {type(reply)}"
    assert reply["header"]["title"]["content"] == "🆘 ClaudeTeam 自定义斜杠命令"
    body = reply["elements"][0]["text"]["content"]
    for c in ("/help", "/team", "/health", "/usage", "/tmux",
              "/send", "/compact", "/recall", "/stop", "/clear"):
        assert c in body


# ── /team ────────────────────────────────────────────────────────


def test_team_classifies_each_pane_state_with_emoji():
    """REGRESSION: /team groups each agent by pane-state emoji + brief.
    Round-80: returns a Feishu card; check the body element for the
    emoji+name+brief lines and the tally summary footer."""
    pane_buffers = {
        "manager": "...\n⏵⏵ bypass permissions on (shift+tab to cycle)\n",
        "worker_cc": "...\nesc to interrupt (1m 12s · ↓ 99 tokens)\n",
        "worker_codex": "(empty)",  # → 🔘
    }

    def fake_capture(target, lines=80):
        return pane_buffers.get(target.window, "")

    with tmux_patch(capture_pane=fake_capture):
        reply = slash.dispatch("/team",
                               _ctx(agents=("manager", "worker_cc", "worker_codex")))

    assert isinstance(reply, dict)
    title = reply["header"]["title"]["content"]
    assert "/team" in title and "员工实时状态" in title
    body = reply["elements"][0]["text"]["content"]
    assert "💤" in body and "manager" in body         # bypass marker → idle
    assert "🔄" in body and "worker_cc" in body       # esc to interrupt → working
    assert "🔘" in body and "worker_codex" in body    # tail-fallback
    assert "3 agents" in body


def test_team_card_color_yellow_when_any_agent_unhealthy():
    """Health colour shortcut: green when every agent is in a healthy
    state (💤/🔄), yellow as soon as one shows ⚠️/🛑/❌. Lets boss
    glance the chat without reading the body."""
    # one agent showing 🛑 (CLI not running)
    pane_buffers = {
        "manager": "...\n⏵⏵ bypass permissions on\n",
        "worker_cc": "$ ",  # bash prompt → 🛑 CLI not running
    }

    def fake_capture(target, lines=80):
        return pane_buffers.get(target.window, "")

    with tmux_patch(capture_pane=fake_capture):
        reply = slash.dispatch("/team",
                               _ctx(agents=("manager", "worker_cc")))
    assert reply["header"]["template"] == "yellow"


# ── /health ──────────────────────────────────────────────────────


def test_health_shells_to_claudeteam_health_and_returns_card():
    captured = {}

    def fake_run(argv, **kwargs):
        captured["argv"] = list(argv)
        return type("R", (), {"returncode": 0, "stdout": "✅ all green", "stderr": ""})()

    reply = slash.dispatch("/health", _ctx(run=fake_run))
    assert captured["argv"] == ["claudeteam", "health"]
    assert isinstance(reply, dict)
    assert "/health" in reply["header"]["title"]["content"]
    body = reply["elements"][0]["text"]["content"]
    assert "✅ all green" in body
    # No ❌ in the output → green template
    assert reply["header"]["template"] == "green"


def test_health_card_is_yellow_when_output_contains_red_or_warn_glyph():
    """The health command emits ❌ for hard fails and ⚠️ for warnings.
    Either flips the card off green so the boss notices."""
    def fake_run_bad(argv, **kwargs):
        return type("R", (), {"returncode": 0,
                              "stdout": "✅ tmux session\n❌ router pid file missing",
                              "stderr": ""})()
    reply = slash.dispatch("/health", _ctx(run=fake_run_bad))
    assert reply["header"]["template"] == "yellow"
    assert "❌" in reply["elements"][0]["text"]["content"]

    def fake_run_warn(argv, **kwargs):
        return type("R", (), {"returncode": 0,
                              "stdout": "✅ team.json: 4 agents\n⚠️  lark_profile blank",
                              "stderr": ""})()
    reply = slash.dispatch("/health", _ctx(run=fake_run_warn))
    assert reply["header"]["template"] == "yellow"


# ── /usage ───────────────────────────────────────────────────────


def test_usage_no_view_shells_with_just_subcommand():
    captured = {}
    fake_run = lambda argv, **kw: (captured.setdefault("argv", list(argv))
                                   or type("R", (), {"returncode": 0,
                                                     "stdout": "x", "stderr": ""})())
    slash.dispatch("/usage", _ctx(run=fake_run))
    assert captured["argv"] == ["claudeteam", "usage"]


def test_usage_view_threads_through_as_flag():
    captured = {}
    fake_run = lambda argv, **kw: (captured.setdefault("argv", list(argv))
                                   or type("R", (), {"returncode": 0,
                                                     "stdout": "x", "stderr": ""})())
    slash.dispatch("/usage daily", _ctx(run=fake_run))
    assert captured["argv"] == ["claudeteam", "usage", "--view", "daily"]


# ── /tmux ────────────────────────────────────────────────────────


def test_tmux_captures_specified_pane():
    captured = {"calls": []}

    def fake_capture(target, lines=80):
        captured["calls"].append((str(target), lines))
        return "line1\nline2\nline3"

    with tmux_patch(capture_pane=fake_capture):
        reply = slash.dispatch("/tmux worker_cc 30", _ctx())
    assert ("ClaudeTeam:worker_cc", 30) in captured["calls"]
    assert "line1\nline2\nline3" in reply
    # Title now matches main: "📺 /tmux worker_cc — 最近 N 行 (SESSION)"
    assert "/tmux worker_cc" in reply
    assert "ClaudeTeam" in reply  # session shown in parens


def test_tmux_unknown_agent_returns_warning():
    reply = slash.dispatch("/tmux ghost", _ctx())
    assert "未知 agent" in reply
    assert "ghost" in reply


def test_tmux_default_agent_is_first_in_team():
    captured = {}

    def fake_capture(target, lines=80):
        captured["target"] = str(target)
        return ""

    with tmux_patch(capture_pane=fake_capture):
        slash.dispatch("/tmux", _ctx(agents=("manager", "worker_cc")))
    assert captured["target"] == "ClaudeTeam:manager"


def test_tmux_clamps_lines_to_max():
    captured = {}

    def fake_capture(target, lines=80):
        captured["lines"] = lines
        return ""

    with tmux_patch(capture_pane=fake_capture):
        slash.dispatch("/tmux manager 99999", _ctx())
    assert captured["lines"] == 2000  # _MAX_TMUX_LINES


# ── /send ────────────────────────────────────────────────────────


def test_send_inject_into_pane():
    captured = {}

    def fake_inject(target, text, **kw):
        captured["target"] = str(target)
        captured["text"] = text
        return True

    with tmux_patch(inject=fake_inject):
        reply = slash.dispatch("/send worker_cc hello world", _ctx())
    assert captured["target"] == "ClaudeTeam:worker_cc"
    assert captured["text"] == "hello world"
    assert "✅" in reply


def test_send_no_args_returns_usage():
    reply = slash.dispatch("/send", _ctx())
    assert "用法:" in reply


def test_send_no_msg_returns_usage():
    reply = slash.dispatch("/send manager", _ctx())
    assert "缺少消息内容" in reply


def test_send_unknown_agent_warns():
    reply = slash.dispatch("/send ghost yo", _ctx())
    assert "未知 agent" in reply


# ── /compact ─────────────────────────────────────────────────────


def test_compact_injects_literal_compact_into_pane():
    captured = []

    def fake_inject(target, text, **kw):
        captured.append((str(target), text))
        return True

    with tmux_patch(inject=fake_inject):
        reply = slash.dispatch("/compact worker_cc", _ctx())
    assert ("ClaudeTeam:worker_cc", "/compact") in captured
    # Default ctx has background=no-op so no second inject for reidentify
    assert len(captured) == 1
    assert "45s 后自动重注 identity" in reply


def test_compact_schedules_background_reidentify_on_success():
    """Round B.2: /compact should schedule a delayed re-injection of
    the identity init prompt so the agent reloads identity.md after
    its self-compact settles."""
    captured = []
    scheduled = []

    def fake_inject(target, text, **kw):
        captured.append((str(target), text))
        return True

    def capture_bg(fn):
        scheduled.append(fn)

    with tmux_patch(inject=fake_inject):
        slash.dispatch("/compact worker_cc", _ctx(background=capture_bg))

        # First inject is /compact; reidentify is queued on background
        assert captured == [("ClaudeTeam:worker_cc", "/compact")]
        assert len(scheduled) == 1

        # Run the queued callback — it should sleep then inject identity prompt
        scheduled[0]()
        assert len(captured) == 2
        target, text = captured[1]
        assert target == "ClaudeTeam:worker_cc"
        assert "You are worker_cc" in text
        assert "agents/worker_cc/identity.md" in text


def test_compact_skips_reidentify_when_inject_fails():
    """If the initial /compact send fails, don't schedule a reidentify."""
    scheduled = []

    def fake_inject(target, text, **kw):
        return False  # simulate tmux send-keys failure

    def capture_bg(fn):
        scheduled.append(fn)

    with tmux_patch(inject=fake_inject):
        reply = slash.dispatch("/compact worker_cc", _ctx(background=capture_bg))
    assert scheduled == []
    assert "45s 后自动重注 identity" not in reply


# ── /stop ────────────────────────────────────────────────────────


def test_stop_sends_ctrl_c():
    captured = {}

    def fake_send_keys(target, *keys, **kw):
        captured["target"] = str(target)
        captured["keys"] = keys
        return True

    with tmux_patch(send_keys=fake_send_keys):
        reply = slash.dispatch("/stop worker_cc", _ctx())
    assert captured["target"] == "ClaudeTeam:worker_cc"
    assert "C-c" in captured["keys"]
    assert "C-c" in reply


def test_stop_no_args_returns_usage():
    reply = slash.dispatch("/stop", _ctx())
    assert "用法:" in reply


# ── /clear ───────────────────────────────────────────────────────


def test_clear_injects_clear_then_init_prompt():
    sequence = []

    def fake_inject(target, text, **kw):
        sequence.append((str(target), text))
        return True

    with tmux_patch(inject=fake_inject):
        reply = slash.dispatch("/clear worker_cc", _ctx())
    # First inject: literal /clear
    assert sequence[0] == ("ClaudeTeam:worker_cc", "/clear")
    # Second inject: identity init prompt — must contain agent name
    assert sequence[1][0] == "ClaudeTeam:worker_cc"
    assert "worker_cc" in sequence[1][1]
    assert "agents/worker_cc/identity.md" in sequence[1][1]
    assert "✅" in reply


# ── unknown / fallback ───────────────────────────────────────────


def test_unknown_slash_returns_help_hint():
    reply = slash.dispatch("/unknownfoo", _ctx())
    assert "未知斜杠命令" in reply
    assert "/help" in reply


# ── /recall (round-95) ──────────────────────────────────────────


def test_recall_no_arg_returns_usage_text():
    """Empty `/recall` is a hint, not an error — show usage as plain
    text (str return) so it threads back as a Feishu reply, not a card."""
    reply = slash.dispatch("/recall", _ctx())
    assert isinstance(reply, str)
    assert "用法: /recall" in reply


def test_recall_unknown_agent_returns_warning():
    reply = slash.dispatch("/recall ghost", _ctx())
    # _bad_agent emits a Chinese warning when name not in agent_set
    assert isinstance(reply, str)
    assert "未知 agent" in reply


def test_recall_with_no_memory_returns_grey_card():
    """Empty memory: card with grey template + helpful nudge to write one.
    Avoid yellow / red because no memory is the default state on a fresh
    deploy, not an alarm."""
    from helpers import isolated_env
    with isolated_env():
        reply = slash.dispatch("/recall manager", _ctx())
    assert isinstance(reply, dict)
    assert reply["header"]["template"] == "grey"
    assert "无记忆" in reply["header"]["title"]["content"]
    body = reply["elements"][0]["text"]["content"]
    assert "claudeteam remember" in body  # nudge


def test_recall_renders_recent_entries_as_card():
    """Populated memory: card with title `/recall <agent> — 最近 N 条`,
    body lists `[ts] [kind] content (ref=X)` per entry. Default N = 10."""
    from helpers import isolated_env
    from claudeteam.store import memory
    with isolated_env():
        memory.append("manager", "task_assigned", "fix login", ref="om_1")
        memory.append("manager", "task_completed", "fix login", ref="om_1")
        reply = slash.dispatch("/recall manager", _ctx())
    assert isinstance(reply, dict)
    title = reply["header"]["title"]["content"]
    assert "/recall manager" in title
    assert "最近 2 条" in title
    body = reply["elements"][0]["text"]["content"]
    assert "[task_assigned]" in body
    assert "[task_completed]" in body
    assert "fix login" in body
    assert "(ref=om_1)" in body


def test_recall_explicit_limit_caps_at_max():
    """`/recall agent N` honours N; over the cap (50) it gets clamped."""
    from helpers import isolated_env
    from claudeteam.store import memory
    with isolated_env():
        for i in range(10):
            memory.append("worker_cc", "note", f"i={i}")
        reply = slash.dispatch("/recall worker_cc 3", _ctx())
    body = reply["elements"][0]["text"]["content"]
    # Only last 3 entries (i=7, 8, 9)
    for i in (7, 8, 9):
        assert f"i={i}" in body
    for i in (0, 1, 2, 3, 4, 5, 6):
        assert f"i={i}" not in body


def test_recall_invalid_limit_returns_warning():
    reply = slash.dispatch("/recall manager not_a_number", _ctx())
    assert isinstance(reply, str)
    assert "N 必须是正整数" in reply


def test_handler_exception_is_caught():
    """A handler that raises mid-flight should produce a graceful warning,
    not propagate. /team now reads tmux panes directly; force capture_pane
    to raise so we exercise the dispatch try/except."""
    def boom_capture(target, lines=80):
        raise RuntimeError("kaboom")
    with tmux_patch(capture_pane=boom_capture):
        reply = slash.dispatch("/team", _ctx())
    # /team's per-agent capture has its own try/except → falls back to
    # empty buffer → tally still works. Use /tmux to exercise the
    # outer dispatch error path instead, since it doesn't catch internally.
    # …actually /tmux's tmux.capture_pane call is unguarded; dispatch
    # outer catch should land it.
    with tmux_patch(capture_pane=boom_capture):
        reply = slash.dispatch("/tmux manager", _ctx())
    assert "slash handler error" in reply or "kaboom" in reply
