"""Tests for feishu/slash.py — router-level slash command dispatch."""
from __future__ import annotations

from helpers import attr_patch, tmux_patch
from claudeteam.feishu import slash
from claudeteam.runtime import tmux


def _ctx(*, agents=("manager", "worker_cc", "worker_codex"),
         session="ClaudeTeam", run=None, sleep=None, background=None,
         lazy_agents=()):
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
        lazy_agents=frozenset(lazy_agents),
        run=fake_run,
        sleep=fake_sleep,
        background=fake_background,
    )


# ── /help ────────────────────────────────────────────────────────


def test_help_returns_card_listing_all_commands():
    """Round-79: /help now returns a Feishu card dict (not text). The card's
    body element is the same _HELP_TEXT block, so command-name search runs
    against `elements[0]['text']['content']` instead of the bare reply.
    Round-95: /recall added — must also appear."""
    reply = slash.dispatch("/help", _ctx())
    assert isinstance(reply, dict), f"/help should return a card dict, got {type(reply)}"
    assert reply["header"]["title"]["content"] == "🆘 ClaudeTeam 自定义斜杠命令"
    body = reply["body"]["elements"][0]["content"]
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
    body = reply["body"]["elements"][0]["content"]
    assert "💤" in body and "manager" in body         # bypass marker → idle
    assert "🔄" in body and "worker_cc" in body       # esc to interrupt → working
    assert "🔘" in body and "worker_codex" in body    # tail-fallback
    assert "3 agents" in body


_BASH_PROMPT = "root@abc123:/app# "  # matches pane_state._BASH_PROMPT_RE


def test_team_card_keeps_green_when_only_unhealthy_is_lazy():
    """Round-129: an agent configured `lazy: true` showing 🛑 because
    its CLI hasn't spawned yet is NOT a failure — flag it ⏸ and keep
    the team header green. R128 smoke surfaced the false-positive."""
    from helpers import isolated_env
    pane_buffers = {
        "manager":     "...\n⏵⏵ bypass permissions on\n",
        "worker_lazy": _BASH_PROMPT,  # → 🛑 pane_state, but lazy = expected
    }

    def fake_capture(target, lines=80):
        return pane_buffers.get(target.window, "")

    team = {"session": "ClaudeTeam", "agents": {
        "manager": {"cli": "claude-code"},
        "worker_lazy": {"cli": "kimi-code", "lazy": True},
    }}
    with isolated_env(team=team), tmux_patch(capture_pane=fake_capture):
        # R158: lazy_agents now flows in via SlashContext (the closure
        # in commands/router.py pre-computes the set at daemon startup
        # so /team's hot path doesn't read team.json). Tests pass it
        # explicitly to mirror that production wiring.
        reply = slash.dispatch("/team",
                               _ctx(agents=("manager", "worker_lazy"),
                                    lazy_agents={"worker_lazy"}))
    assert reply["header"]["template"] == "green"
    body = reply["body"]["elements"][0]["content"]
    # Lazy worker shown with ⏸ glyph (not 🛑) and a "lazy" hint
    assert "⏸" in body
    assert "worker_lazy" in body
    assert "lazy" in body.lower()


def test_team_card_still_yellow_for_truly_dead_pane():
    """The lazy exception must NOT shadow real failures. A non-lazy
    agent whose CLI is actually dead (🛑) still flips to yellow."""
    from helpers import isolated_env
    pane_buffers = {
        "manager": "...\n⏵⏵ bypass permissions on\n",
        "worker_cc": _BASH_PROMPT,  # NOT lazy in team.json → real failure
    }

    def fake_capture(target, lines=80):
        return pane_buffers.get(target.window, "")

    team = {"session": "ClaudeTeam", "agents": {
        "manager": {"cli": "claude-code"},
        "worker_cc": {"cli": "claude-code"},  # no lazy
    }}
    with isolated_env(team=team), tmux_patch(capture_pane=fake_capture):
        reply = slash.dispatch("/team",
                               _ctx(agents=("manager", "worker_cc")))
    assert reply["header"]["template"] == "yellow"
    body = reply["body"]["elements"][0]["content"]
    assert "🛑" in body  # honest failure glyph kept


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


# ── /health (R166: server-load card with column_set 3 grid) ──────


def _stub_server_load(monkey_data: dict):
    """Patch `runtime.server_metrics.collect_server_load` for the
    duration of the test so /health's data comes from `monkey_data`
    instead of host shell-outs."""
    from helpers import attr_patch
    from claudeteam.runtime import server_metrics
    return attr_patch(server_metrics,
                      collect_server_load=lambda agent_set=None, session=None,
                      run=None: monkey_data)


def test_health_card_renders_host_section_with_column_set():
    """R166: /health card has 🖥️ 主机总览 + column_set 3 (CPU/内存/磁盘)
    + colored percentage spans. No more text dump."""
    data = {
        "host": {
            "cpu": {"load": (1.2, 0.8, 0.5), "cores": 8, "pct": 15},
            "mem": {"total": 16 * 1024**3, "used": 8 * 1024**3,
                    "available": 7 * 1024**3, "pct": 50,
                    "swap": {"total": 0, "used": 0}},
            "disk": {"mount": "/", "used": 100 * 1024**3,
                     "total": 500 * 1024**3, "pct": 20},
        },
        "containers": [], "agents": [], "alarms": [],
    }
    with _stub_server_load(data):
        reply = slash.dispatch("/health", _ctx())
    assert isinstance(reply, dict)
    assert reply["schema"] == "2.0"
    assert reply["header"]["template"] == "purple"  # default no-alarm
    title = reply["header"]["title"]["content"]
    assert "/health" in title and "服务器负载" in title
    # First element is the section heading, second is the column_set grid
    elements = reply["body"]["elements"]
    headings = [e for e in elements if e.get("tag") == "markdown"
                and "**🖥" in e.get("content", "")]
    assert headings, "missing 🖥️ 主机总览 heading"
    col_sets = [e for e in elements if e.get("tag") == "column_set"]
    assert col_sets, "missing column_set rows"
    # First grid has 3 columns matching (CPU/内存/磁盘)
    first_grid = col_sets[0]
    assert len(first_grid["columns"]) == 3
    # Each cell has a markdown element
    for col in first_grid["columns"]:
        assert col["elements"][0]["tag"] == "markdown"


def test_health_card_includes_alarm_section_when_alarms_present():
    """Alarms in the data dict surface as a 🚨 section AND flip header
    to yellow so the boss notices something's wrong at a glance."""
    data = {
        "host": {"cpu": None, "mem": None, "disk": None},
        "containers": [],
        "agents": [],
        "alarms": ["主机内存 **92%**", "磁盘 `/var` **85%**"],
    }
    with _stub_server_load(data):
        reply = slash.dispatch("/health", _ctx())
    assert reply["header"]["template"] == "yellow"
    contents = " ".join(e.get("content", "")
                        for e in reply["body"]["elements"]
                        if e.get("tag") == "markdown")
    assert "🚨" in contents
    assert "主机内存" in contents
    assert "85%" in contents


def test_health_card_falls_back_to_no_data_cells_when_host_empty():
    """When uptime/free/df all returned None (Docker Desktop on macOS
    can hit this), the host section still renders with 无数据 cells
    instead of crashing or showing an empty grid."""
    data = {
        "host": {"cpu": None, "mem": None, "disk": None},
        "containers": [], "agents": [], "alarms": [],
    }
    with _stub_server_load(data):
        reply = slash.dispatch("/health", _ctx())
    contents = " ".join(e.get("content", "")
                        for col_set in reply["body"]["elements"]
                        if col_set.get("tag") == "column_set"
                        for col in col_set["columns"]
                        for e in col["elements"])
    assert contents.count("无数据") >= 3  # CPU + 内存 + 磁盘 all blank


def test_health_card_emits_v2_schema_with_grey_footer():
    """Footer line records collection time + data source list — useful
    for debug "is this card stale?" questions. v2 schema dropped the
    v1 `note` tag, so we use a grey-font markdown line as the footer."""
    data = {"host": {"cpu": None, "mem": None, "disk": None},
            "containers": [], "agents": [], "alarms": []}
    with _stub_server_load(data):
        reply = slash.dispatch("/health", _ctx())
    # The last element should carry the footer text in a grey font span.
    last = reply["body"]["elements"][-1]
    assert last["tag"] == "markdown"
    assert "采集" in last["content"]
    assert "uptime/free/df/docker stats/ps" in last["content"]
    assert "color='grey'" in last["content"]


# ── /usage (R167: rich card with column_set 2 + ccusage summary) ─


def _usage_run(json_payload: str):
    """Stub `ctx.run` to return JSON of `claudeteam usage --json`."""
    return lambda argv, **kw: type("R", (), {
        "returncode": 0, "stdout": json_payload, "stderr": ""})()


def test_usage_no_view_shells_claudeteam_usage_json():
    """R167: handler shells out with `--json` so the card builder gets
    structured data, not raw text."""
    captured = {}
    fake_run = lambda argv, **kw: (captured.setdefault("argv", list(argv))
                                   or type("R", (), {"returncode": 0,
                                                     "stdout": '{}', "stderr": ""})())
    slash.dispatch("/usage", _ctx(run=fake_run))
    assert captured["argv"][:3] == ["claudeteam", "usage", "--json"]


def test_usage_view_threads_through_view_flag():
    captured = {}
    fake_run = lambda argv, **kw: (captured.setdefault("argv", list(argv))
                                   or type("R", (), {"returncode": 0,
                                                     "stdout": '{}', "stderr": ""})())
    slash.dispatch("/usage daily", _ctx(run=fake_run))
    assert captured["argv"] == ["claudeteam", "usage", "--json",
                                 "--view", "daily"]


def test_usage_card_emits_purple_header_when_ccusage_ok():
    """R167: matches main's /usage card branding — purple header.
    No more blue / plain-body / fenced fallback."""
    payload = ('{"view":"daily","claude_code":{"ok":true,"rc":0,'
               '"output":"Date | Cost\\n2026-05-04 | $0.42\\nTotal: $1.23"},'
               '"other_clis":[]}')
    reply = slash.dispatch("/usage", _ctx(run=_usage_run(payload)))
    assert isinstance(reply, dict)
    assert reply["schema"] == "2.0"
    assert reply["header"]["template"] == "purple"
    title = reply["header"]["title"]["content"]
    assert "/usage" in title and "(daily)" in title


def test_usage_card_extracts_total_from_ccusage_output():
    """Total line gets surfaced as a column_set 2 row with the dollar
    amount in blue. Boss reads the bottom-line cost at a glance instead
    of scanning a multi-line table."""
    payload = ('{"view":"daily","claude_code":{"ok":true,"rc":0,'
               '"output":"Date | Cost\\n2026-05-04 | $0.42\\nTotal: $1.23"},'
               '"other_clis":[]}')
    reply = slash.dispatch("/usage", _ctx(run=_usage_run(payload)))
    elements = reply["body"]["elements"]
    col_sets = [e for e in elements if e.get("tag") == "column_set"]
    assert col_sets, "missing column_set row"
    # First row's right cell should contain the total
    right_content = col_sets[0]["columns"][1]["elements"][0]["content"]
    assert "$1.23" in right_content
    assert "color='blue'" in right_content


def test_usage_card_summarises_ccusage_failure_to_one_line():
    """ccusage's npm WARN + Node stack trace gets boiled down to one
    operator-readable line in red — boss flagged the raw 30-line dump
    as ugly. Header flips red so the failure is visible at glance."""
    payload = ('{"view":"daily","claude_code":{"ok":false,"rc":1,'
               '"output":"npm WARN EBADENGINE Unsupported engine\\n'
               'npm WARN ...\\n'
               'Error: No valid Claude data directories found\\n'
               '   at getClaudePaths\\n"},'
               '"other_clis":[]}')
    reply = slash.dispatch("/usage", _ctx(run=_usage_run(payload)))
    assert reply["header"]["template"] == "red"
    elements = reply["body"]["elements"]
    col_sets = [e for e in elements if e.get("tag") == "column_set"]
    right_content = col_sets[0]["columns"][1]["elements"][0]["content"]
    assert "color='red'" in right_content
    assert "ccusage 失败" in right_content
    # The actual error line surfaces
    assert "No valid Claude data directories" in right_content
    # WARN noise does NOT surface
    assert "EBADENGINE" not in right_content


def test_usage_card_includes_other_cli_section_when_present():
    """`other_clis` from `claudeteam usage --json` (non-claude-code
    agents) render as their own section with column_set 2 rows."""
    payload = ('{"view":"daily","claude_code":null,'
               '"other_clis":['
               '{"cli":"codex-cli","note":"no upstream usage tool"},'
               '{"cli":"kimi-code","note":"no upstream usage tool"}'
               ']}')
    reply = slash.dispatch("/usage", _ctx(run=_usage_run(payload)))
    contents = " ".join(e.get("content", "") for e in reply["body"]["elements"]
                        if e.get("tag") == "markdown")
    assert "📦 其他 CLI" in contents
    col_sets = [e for e in reply["body"]["elements"]
                if e.get("tag") == "column_set"]
    # Two rows for two CLIs
    cli_names = [cs["columns"][0]["elements"][0]["content"]
                 for cs in col_sets]
    assert "**codex-cli**" in cli_names
    assert "**kimi-code**" in cli_names


def test_usage_card_renders_no_data_when_both_sections_empty():
    """No claude-code config + no other CLIs → render `(无数据)` rather
    than an empty card body."""
    payload = '{"view":"daily","claude_code":null,"other_clis":[]}'
    reply = slash.dispatch("/usage", _ctx(run=_usage_run(payload)))
    contents = " ".join(e.get("content", "") for e in reply["body"]["elements"]
                        if e.get("tag") == "markdown")
    assert "(无数据)" in contents


def test_usage_card_handles_invalid_json_gracefully():
    """Shell-out returned non-JSON (e.g. claudeteam usage crashed) →
    fall back to empty data; render the no-data placeholder + footer
    instead of crashing."""
    bad_run = lambda argv, **kw: type("R", (), {
        "returncode": 0, "stdout": "not json {[", "stderr": ""})()
    reply = slash.dispatch("/usage", _ctx(run=bad_run))
    assert isinstance(reply, dict)
    contents = " ".join(e.get("content", "") for e in reply["body"]["elements"]
                        if e.get("tag") == "markdown")
    assert "(无数据)" in contents


# ── /tmux ────────────────────────────────────────────────────────


def test_tmux_captures_specified_pane():
    """Round-116: /tmux returns a blue card with fenced pane body so
    the monospace pane content (spinner / box drawing / banners)
    renders aligned in Feishu."""
    captured = {"calls": []}

    def fake_capture(target, lines=80):
        captured["calls"].append((str(target), lines))
        return "line1\nline2\nline3"

    with tmux_patch(capture_pane=fake_capture):
        reply = slash.dispatch("/tmux worker_cc 30", _ctx())
    assert ("ClaudeTeam:worker_cc", 30) in captured["calls"]
    assert isinstance(reply, dict)
    assert reply["header"]["template"] == "blue"
    title = reply["header"]["title"]["content"]
    assert "/tmux worker_cc" in title
    assert "ClaudeTeam" in title  # session shown in brackets
    body = reply["body"]["elements"][0]["content"]
    assert "```" in body  # fenced
    assert "line1\nline2\nline3" in body


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
    body = reply["body"]["elements"][0]["content"]
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
    body = reply["body"]["elements"][0]["content"]
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
    body = reply["body"]["elements"][0]["content"]
    # Only last 3 entries (i=7, 8, 9)
    for i in (7, 8, 9):
        assert f"i={i}" in body
    for i in (0, 1, 2, 3, 4, 5, 6):
        assert f"i={i}" not in body


def test_recall_invalid_limit_returns_warning():
    reply = slash.dispatch("/recall manager not_a_number", _ctx())
    assert isinstance(reply, str)
    assert "N 必须是正整数" in reply


# ── /recall --kind filter (round-108) ────────────────────────────


def test_recall_kind_filter_narrows_results():
    """Round-108: /recall --kind K filters to entries matching that kind.
    Title carries `kind=K` so boss sees what was queried."""
    from helpers import isolated_env
    from claudeteam.store import memory
    with isolated_env():
        memory.append("manager", "decision", "use bcrypt")
        memory.append("manager", "learning", "auth path /auth")
        memory.append("manager", "decision", "rotate keys monthly")
        reply = slash.dispatch("/recall manager --kind decision", _ctx())
    assert isinstance(reply, dict)
    title = reply["header"]["title"]["content"]
    assert "kind=decision" in title
    body = reply["body"]["elements"][0]["content"]
    assert "use bcrypt" in body
    assert "rotate keys monthly" in body
    assert "auth path" not in body


def test_recall_kind_filter_argument_order_flexible():
    """Both `worker_cc --kind blocker 5` and `--kind blocker worker_cc 5`
    work — flag position shouldn't matter."""
    from helpers import isolated_env
    from claudeteam.store import memory
    with isolated_env():
        memory.append("worker_cc", "blocker", "missing PAT")
        for layout in (
            "/recall worker_cc --kind blocker",
            "/recall --kind blocker worker_cc",
            "/recall worker_cc 5 --kind blocker",
        ):
            reply = slash.dispatch(layout, _ctx())
            assert isinstance(reply, dict), f"layout={layout!r} → {type(reply)}"
            body = reply["body"]["elements"][0]["content"]
            assert "missing PAT" in body, f"layout={layout!r}"


def test_recall_kind_with_no_value_returns_warning():
    """`--kind` with no following token → warning, not silent default."""
    reply = slash.dispatch("/recall manager --kind", _ctx())
    assert isinstance(reply, str)
    assert "--kind needs a value" in reply


def test_recall_kind_unknown_inline_notes_in_card():
    """Unconventional kind: card still renders (free-form entries DO
    exist) but body shows a typo-guard line with KNOWN_KINDS list."""
    from helpers import isolated_env
    from claudeteam.store import memory
    with isolated_env():
        memory.append("manager", "fyi", "non-canonical entry")
        reply = slash.dispatch("/recall manager --kind fyi", _ctx())
    assert isinstance(reply, dict)
    body = reply["body"]["elements"][0]["content"]
    assert "fyi" in body
    # KNOWN_KINDS hint visible
    assert "decision" in body  # one of KNOWN_KINDS list rendering
    assert "约定" in body or "kind=" in body


# ── /forget (round-112) ─────────────────────────────────────────


def test_forget_no_args_returns_usage_text():
    reply = slash.dispatch("/forget", _ctx())
    assert isinstance(reply, str)
    assert "用法: /forget" in reply
    # Convention list visible so operator sees what kinds exist
    for k in ("decision", "blocker", "learning", "note"):
        assert k in reply


def test_forget_unknown_agent_returns_warning():
    reply = slash.dispatch("/forget ghost --yes", _ctx())
    assert isinstance(reply, str)
    assert "未知 agent" in reply


def test_forget_without_yes_returns_grey_confirm_card():
    """Round-112 safety gate: /forget without --yes never wipes; shows
    a grey confirm card with the exact reissue string."""
    from helpers import isolated_env
    from claudeteam.store import memory
    with isolated_env():
        memory.append("manager", "decision", "important")
        reply = slash.dispatch("/forget manager", _ctx())
    assert isinstance(reply, dict)
    assert reply["header"]["template"] == "grey"
    assert "确认前不会执行" in reply["header"]["title"]["content"]
    body = reply["body"]["elements"][0]["content"]
    assert "/forget manager --yes" in body
    # Memory NOT touched
    with isolated_env():
        # (re-isolate; previous block had its own env)
        pass


def test_forget_without_yes_does_not_mutate_memory():
    """Pinned separately: confirm-card path is read-only."""
    from helpers import isolated_env
    from claudeteam.store import memory
    with isolated_env():
        memory.append("manager", "decision", "x")
        slash.dispatch("/forget manager", _ctx())
        slash.dispatch("/forget manager --kind decision", _ctx())
        assert len(memory.list_recent("manager")) == 1


def test_forget_yes_wipes_all_returns_red_card():
    from helpers import isolated_env
    from claudeteam.store import memory
    with isolated_env():
        memory.append("manager", "decision", "a")
        memory.append("manager", "note", "b")
        reply = slash.dispatch("/forget manager --yes", _ctx())
    assert isinstance(reply, dict)
    assert reply["header"]["template"] == "red"
    body = reply["body"]["elements"][0]["content"]
    assert "已清掉" in body and "2" in body


def test_forget_yes_with_kind_drops_only_slice():
    from helpers import isolated_env
    from claudeteam.store import memory
    with isolated_env():
        memory.append("manager", "decision", "a")
        memory.append("manager", "decision", "b")
        memory.append("manager", "note", "c")
        reply = slash.dispatch("/forget manager --kind decision --yes",
                               _ctx())
        assert reply["header"]["template"] == "red"
        body = reply["body"]["elements"][0]["content"]
        assert "2" in body and "decision" in body
        # `note` survived
        rows = memory.list_recent("manager")
        assert [r["kind"] for r in rows] == ["note"]


def test_forget_yes_no_match_returns_grey_card():
    """Empty-match wipe is a no-op — grey card, no claims of removal."""
    from helpers import isolated_env
    from claudeteam.store import memory
    with isolated_env():
        memory.append("manager", "note", "x")
        reply = slash.dispatch("/forget manager --kind decision --yes",
                               _ctx())
    assert reply["header"]["template"] == "grey"
    assert "无事可做" in reply["body"]["elements"][0]["content"]


def test_forget_kind_no_value_returns_warning():
    reply = slash.dispatch("/forget manager --kind", _ctx())
    assert isinstance(reply, str)
    assert "--kind needs a value" in reply


def test_help_text_now_advertises_forget():
    """Round-112: /help card body must list /forget so boss can discover
    it without grepping source."""
    reply = slash.dispatch("/help", _ctx())
    body = reply["body"]["elements"][0]["content"]
    assert "/forget" in body


def test_recall_kind_no_match_returns_grey_card_with_filter_label():
    from helpers import isolated_env
    from claudeteam.store import memory
    with isolated_env():
        memory.append("manager", "note", "only a note")
        reply = slash.dispatch("/recall manager --kind decision", _ctx())
    assert reply["header"]["template"] == "grey"
    title = reply["header"]["title"]["content"]
    assert "kind=decision" in title


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
