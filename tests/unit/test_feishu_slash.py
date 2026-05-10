"""Tests for feishu/slash.py — router-level slash command dispatch."""
from __future__ import annotations

from helpers import attr_patch, tmux_patch
from claudeteam.feishu import slash
from claudeteam.runtime import tmux


def _elements(reply):
    """R172: card-shape adapter. Both simple_card and rich_card now
    return v2 (`body.elements`). Helper kept for legacy tests + future-
    proofing if we ever flip a builder back to v1."""
    if "elements" in reply:
        return reply["elements"]
    return reply.get("body", {}).get("elements", [])


def _all_markdown(reply) -> str:
    """Concatenate every `tag: markdown` element's content. R172.b:
    column_set was dropped, so all card body content lives in plain
    markdown elements — this helper lets tests assert on text
    substrings without caring about element ordering or layout."""
    return "\n".join(e.get("content", "") for e in _elements(reply)
                     if e.get("tag") == "markdown")


def _body_text(reply) -> str:
    """Pull the first element's text content, tolerant of both card
    shapes. simple_card reverted to v1 (lark_md div) on 2026-05-10
    after the v2 markdown element triggered Feishu's '请升级' fallback
    in the boss's tenant; rich_card stayed v2 (multi-section caller).
    Tests run mixed handlers so this normaliser keeps assertions
    short across both shapes."""
    elts = _elements(reply)
    if not elts:
        return ""
    first = elts[0]
    if isinstance(first.get("text"), dict):
        return first["text"].get("content", "")
    return first.get("content", "")


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
    """R172.b: /help lists main's exact 9-command surface — `/recall`
    and `/forget` were dropped per boss feedback (not in main, not
    requested)."""
    reply = slash.dispatch("/help", _ctx())
    assert isinstance(reply, dict), f"/help should return a card dict, got {type(reply)}"
    assert reply["header"]["title"]["content"] == "🆘 ClaudeTeam 自定义斜杠命令"
    body = _body_text(reply)
    for c in ("/help", "/team", "/health", "/usage", "/tmux",
              "/send", "/compact", "/stop", "/clear"):
        assert c in body
    # Dropped commands stay dropped
    assert "/recall" not in body
    assert "/forget" not in body


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
    body = _body_text(reply)
    assert "💤" in body and "manager" in body         # bypass marker → idle
    assert "🔄" in body and "worker_cc" in body       # esc to interrupt → working
    assert "🔘" in body and "worker_codex" in body    # tail-fallback
    assert "3 agents" in body


_BASH_PROMPT = "root@abc123:/app# "  # matches pane_state._BASH_PROMPT_RE


def test_team_card_reflects_live_toml_after_adding_agent():
    """REGRESSION: previously /team handler used ctx.team_agents +
    ctx.lazy_agents pre-computed at router startup, so editing
    claudeteam.toml to add a new agent did NOT show up until restart.
    Boss-flagged: a config file is meant to live-edit. Now /team
    re-reads team config every call."""
    from helpers import isolated_env
    pane_buffers = {
        "manager":      "...\n⏵⏵ bypass permissions on\n",
        "worker_cc":    "...\n⏵⏵ bypass permissions on\n",
        "worker_codex": "...\n⏵⏵ bypass permissions on\n",
    }
    def fake_capture(target, lines=80):
        return pane_buffers.get(target.window, "")

    # Initial config: 2 agents
    team = {"session": "ClaudeTeam", "agents": {
        "manager":   {"cli": "claude-code"},
        "worker_cc": {"cli": "claude-code"},
    }}
    with isolated_env(team=team), tmux_patch(capture_pane=fake_capture):
        # ctx still has stale 2-agent list; but handler should ignore
        # ctx and re-read from disk → see exactly the 2 agents.
        reply1 = slash.dispatch("/team",
                                _ctx(agents=("manager", "worker_cc")))
        body1 = _body_text(reply1)
        assert "2 agents" in body1
        assert "worker_codex" not in body1

        # Now operator edits claudeteam.toml to add worker_codex.
        from claudeteam.runtime import config as _config, paths
        from claudeteam.runtime import tunables as _tun
        team["agents"]["worker_codex"] = {"cli": "codex-cli"}
        # Refresh whichever shape isolated_env wrote (json or toml). We
        # write a minimal toml that load_team can pick up regardless.
        cf = paths.config_file()
        toml_lines = ['[team]\nsession = "ClaudeTeam"']
        for n, c in team["agents"].items():
            toml_lines.append(f'\n[team.agents.{n}]')
            for k, v in c.items():
                toml_lines.append(
                    f'{k} = {repr(v) if not isinstance(v, str) else chr(34)+v+chr(34)}'.replace("'", '"'))
        cf.write_text('\n'.join(toml_lines), encoding='utf-8')
        _tun.reset_cache()

        # Same ctx (stale 2-agent), but handler reads disk → 3 agents.
        reply2 = slash.dispatch("/team",
                                _ctx(agents=("manager", "worker_cc")))
        body2 = _body_text(reply2)
        assert "3 agents" in body2
        assert "worker_codex" in body2


def test_team_card_drops_agent_removed_from_toml_live():
    """REGRESSION (reverse direction): removing an agent block from
    claudeteam.toml should make the next /team stop listing it.
    Without _live_agents() reading config fresh, the daemon's startup
    cache would keep showing the now-deleted agent forever."""
    from helpers import isolated_env
    from claudeteam.runtime import paths, tunables as _tun

    pane_buffers = {
        "manager":   "...\n⏵⏵ bypass permissions on\n",
        "worker_cc": "...\n⏵⏵ bypass permissions on\n",
        "worker_codex": "...\n⏵⏵ bypass permissions on\n",
    }
    def fake_capture(target, lines=80):
        return pane_buffers.get(target.window, "")

    team = {"session": "ClaudeTeam", "agents": {
        "manager":      {"cli": "claude-code"},
        "worker_cc":    {"cli": "claude-code"},
        "worker_codex": {"cli": "codex-cli"},
    }}
    with isolated_env(team=team), tmux_patch(capture_pane=fake_capture):
        # 3 agents
        reply1 = slash.dispatch("/team",
                                _ctx(agents=("manager", "worker_cc", "worker_codex")))
        body1 = _body_text(reply1)
        assert "3 agents" in body1
        assert "worker_codex" in body1

        # Operator deletes worker_codex from claudeteam.toml live
        cf = paths.config_file()
        cf.write_text(
            '[team]\nsession = "ClaudeTeam"\n\n'
            '[team.agents.manager]\ncli = "claude-code"\n\n'
            '[team.agents.worker_cc]\ncli = "claude-code"\n',
            encoding='utf-8')
        _tun.reset_cache()

        # Stale ctx still says 3 agents, but live read sees 2
        reply2 = slash.dispatch("/team",
                                _ctx(agents=("manager", "worker_cc", "worker_codex")))
        body2 = _body_text(reply2)
        assert "2 agents" in body2
        assert "worker_codex" not in body2


def test_team_card_reflects_lazy_flag_added_to_toml_live():
    """Adding `lazy = true` to an agent in claudeteam.toml should
    flip its /team glyph to ⏸ immediately — no router restart.
    The team card stays green because lazy is by design."""
    from helpers import isolated_env
    from claudeteam.runtime import paths, tunables as _tun

    pane_buffers = {
        "manager":   "...\n⏵⏵ bypass permissions on\n",
        "worker_cc": _BASH_PROMPT,  # bare shell → 🛑 unless lazy
    }
    def fake_capture(target, lines=80):
        return pane_buffers.get(target.window, "")

    team = {"session": "ClaudeTeam", "agents": {
        "manager":   {"cli": "claude-code"},
        "worker_cc": {"cli": "claude-code"},
    }}
    with isolated_env(team=team), tmux_patch(capture_pane=fake_capture):
        # Before: worker_cc is not lazy, bash prompt → 🛑 → yellow team
        reply1 = slash.dispatch("/team",
                                _ctx(agents=("manager", "worker_cc")))
        assert reply1["header"]["template"] == "yellow"
        body1 = _body_text(reply1)
        assert "🛑" in body1

        # Operator edits toml: mark worker_cc lazy
        cf = paths.config_file()
        cf.write_text(
            '[team]\nsession = "ClaudeTeam"\n\n'
            '[team.agents.manager]\ncli = "claude-code"\n\n'
            '[team.agents.worker_cc]\ncli = "claude-code"\nlazy = true\n',
            encoding='utf-8')
        _tun.reset_cache()

        # After: lazy flag picked up live → ⏸ glyph + green team
        reply2 = slash.dispatch("/team",
                                _ctx(agents=("manager", "worker_cc")))
        assert reply2["header"]["template"] == "green"
        body2 = _body_text(reply2)
        assert "⏸" in body2
        assert "🛑" not in body2


def test_tmux_recognises_agent_added_to_toml_without_restart():
    """REGRESSION: /tmux <new_agent> previously rejected agents added
    to claudeteam.toml after router started, because _bad_agent used
    ctx.agent_set (cached at daemon boot). Now _live_agents() reads
    config fresh so live-edits show up."""
    from helpers import isolated_env
    from claudeteam.runtime import paths, tunables as _tun

    team = {"session": "ClaudeTeam", "agents": {
        "manager": {"cli": "claude-code"},
    }}
    pane_buffers = {"manager": "x", "worker_new": "from new pane"}
    def fake_capture(target, lines=80):
        return pane_buffers.get(target.window, "")

    with isolated_env(team=team), tmux_patch(capture_pane=fake_capture):
        # Old ctx still says only "manager" — handler must ignore
        # ctx and re-resolve from disk.
        reply_known_only = slash.dispatch("/tmux worker_new",
                                          _ctx(agents=("manager",)))
        # Initially worker_new isn't in toml → expect 未知 agent warning
        assert "未知 agent" in str(reply_known_only)

        # Operator adds worker_new to claudeteam.toml live.
        cf = paths.config_file()
        cf.write_text(
            '[team]\nsession = "ClaudeTeam"\n\n'
            '[team.agents.manager]\ncli = "claude-code"\n\n'
            '[team.agents.worker_new]\ncli = "claude-code"\n',
            encoding='utf-8')
        _tun.reset_cache()

        # Same stale ctx, but /tmux now sees the new agent because
        # _bad_agent goes through _live_agents() — no restart needed.
        reply_after = slash.dispatch("/tmux worker_new",
                                     _ctx(agents=("manager",)))
        assert "未知 agent" not in str(reply_after)
        # And the captured pane content shows up in the card
        assert "from new pane" in str(reply_after)


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
    body = _body_text(reply)
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
    body = _body_text(reply)
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


def test_health_card_renders_host_section_with_cpu_mem_disk():
    """R166/R172.b: /health card has 🖥️ 主机总览 with CPU + 内存 +
    磁盘 metrics. Original R166 used `column_set 3`; R172.b dropped
    column_set (Feishu's renderer collapsed it anyway) so cells now
    render as paragraph-separated markdown — assertions look for the
    label/value substrings rather than column structure."""
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
    assert reply["header"]["template"] == "purple"  # default no-alarm
    title = reply["header"]["title"]["content"]
    assert "/health" in title and "服务器负载" in title
    blob = _all_markdown(reply)
    assert "🖥️ 主机总览" in blob
    assert "**CPU**" in blob and "1.20 / 8 核" in blob
    assert "**内存**" in blob and "16.00 GB" in blob
    assert "**磁盘**" in blob and "/" in blob


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
                        for e in _elements(reply)
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
    blob = _all_markdown(reply)
    assert blob.count("无数据") >= 3  # CPU + 内存 + 磁盘 all blank


def test_health_card_emits_grey_footer():
    """Footer line records collection time + data source list — useful
    for debug "is this card stale?" questions. We use a grey-font
    markdown line as the footer (v1's `note` tag was dropped during
    R159; we kept the grey-font shape across the R172 v1-revert)."""
    data = {"host": {"cpu": None, "mem": None, "disk": None},
            "containers": [], "agents": [], "alarms": []}
    with _stub_server_load(data):
        reply = slash.dispatch("/health", _ctx())
    # The last element should carry the footer text in a grey font span.
    last = _elements(reply)[-1]
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


def test_usage_card_emits_purple_header_when_cc_ok():
    """R173: card branding stays purple when CC usage probe succeeds.
    Header flips red on any per-CLI failure."""
    payload = ('{"view":"daily","claude_code":{"ok":true,"metrics":['
               '{"label":"5-hour window","used_pct":40,"remaining_pct":60,'
               '"reset_iso":"2026-05-05T18:00:00Z"}]},'
               '"other_clis":[]}')
    reply = slash.dispatch("/usage", _ctx(run=_usage_run(payload)))
    assert isinstance(reply, dict)
    assert reply["header"]["template"] == "purple"
    title = reply["header"]["title"]["content"]
    assert "/usage" in title and "(daily)" in title


def test_usage_card_renders_cc_metrics_with_traffic_light():
    """R173: real per-window utilization replaces ccusage Total. Each
    metric gets `**剩余 X%**` with traffic-light color (green > orange
    > red as remaining drops)."""
    payload = ('{"view":"daily","claude_code":{"ok":true,"metrics":['
               '{"label":"5-hour window","used_pct":40,"remaining_pct":60,'
               '"reset_iso":"2026-05-05T18:00:00Z"},'
               '{"label":"7-day all models","used_pct":85,"remaining_pct":15,'
               '"reset_iso":"2026-05-12T00:00:00Z"}]},'
               '"other_clis":[]}')
    reply = slash.dispatch("/usage", _ctx(run=_usage_run(payload)))
    blob = _all_markdown(reply)
    assert "5-hour window" in blob
    assert "剩余 60%" in blob
    assert "color='green'" in blob    # >50 remaining = green
    assert "7-day all models" in blob
    assert "剩余 15%" in blob
    assert "color='red'" in blob       # ≤20 remaining = red


def test_usage_card_renders_cc_extra_usage_dollar_block():
    """R173: extra_usage block (non-Max paid burst) renders as
    `已用 X% · $used / $cap CCY` for Max-Pro pay-as-you-go visibility."""
    payload = ('{"view":"daily","claude_code":{"ok":true,"metrics":['
               '{"label":"Extra usage","used_pct":12,"remaining_pct":88,'
               '"reset_iso":"","extra":{"used":3.45,"cap":50,"ccy":"USD"}}]},'
               '"other_clis":[]}')
    reply = slash.dispatch("/usage", _ctx(run=_usage_run(payload)))
    blob = _all_markdown(reply)
    assert "Extra usage" in blob
    assert "$3.45 / $50" in blob
    assert "已用 12%" in blob


def test_usage_card_marks_header_red_when_cc_failed():
    """Auth expired / network down → ok=False with note; header flips
    to red so boss notices in the chat title."""
    payload = ('{"view":"daily","claude_code":{"ok":false,'
               '"note":"access token 已过期 (2026-05-05 05:56)"},'
               '"other_clis":[]}')
    reply = slash.dispatch("/usage", _ctx(run=_usage_run(payload)))
    assert reply["header"]["template"] == "red"
    blob = _all_markdown(reply)
    assert "Claude usage 读取失败" in blob
    assert "已过期" in blob


def test_usage_card_includes_other_cli_section_when_present():
    """`other_clis` from `claudeteam usage --json` (non-claude-code
    agents) render as a 📦 其他 CLI section with one row per CLI."""
    payload = ('{"view":"daily","claude_code":null,'
               '"other_clis":['
               '{"cli":"codex-cli","note":"no upstream usage tool"},'
               '{"cli":"kimi-code","note":"no upstream usage tool"}'
               ']}')
    reply = slash.dispatch("/usage", _ctx(run=_usage_run(payload)))
    blob = _all_markdown(reply)
    assert "📦 其他 CLI" in blob
    assert "**codex-cli**" in blob
    assert "**kimi-code**" in blob
    assert blob.count("no upstream usage tool") == 2


def test_usage_card_renders_no_data_when_both_sections_empty():
    """No claude-code config + no other CLIs → render `(无数据)` rather
    than an empty card body."""
    payload = '{"view":"daily","claude_code":null,"other_clis":[]}'
    reply = slash.dispatch("/usage", _ctx(run=_usage_run(payload)))
    assert "(无数据)" in _all_markdown(reply)


def test_usage_card_renders_codex_section_with_metrics():
    """R173: codex section now surfaces real % consumed per limit
    window (5h / Weekly / etc) — not just plan + email. Boss flagged
    the R170 plan-only output as useless ('登录账号有屁用啊')."""
    payload = ('{"view":"daily","claude_code":null,'
               '"codex":{"ok":true,"plan":"ChatGPT Pro","metrics":['
               '{"label":"5h limit","used_pct":20,"remaining_pct":80,"reset":"4h"},'
               '{"label":"Weekly limit","used_pct":35,"remaining_pct":65,"reset":"5d"}'
               ']},'
               '"other_clis":[]}')
    reply = slash.dispatch("/usage", _ctx(run=_usage_run(payload)))
    blob = _all_markdown(reply)
    assert "🟦 Codex" in blob
    assert "ChatGPT Pro" in blob
    # Per-window metrics with traffic-light colored remaining-%
    assert "5h limit" in blob
    assert "剩余 80%" in blob
    assert "已用 20%" in blob
    assert "Weekly limit" in blob
    assert "剩余 65%" in blob


def test_usage_card_renders_kimi_section_with_quota_metrics():
    """R170: each kimi metric appears with a traffic-light
    remaining-percent. R172.b: as one-line markdown rows."""
    payload = ('{"view":"daily","claude_code":null,'
               '"kimi":{"ok":true,"metrics":[{'
               '"label":"Weekly limit","used":2,"limit":10,'
               '"used_pct":20,"remaining_pct":80,'
               '"reset_iso":"2026-05-08T00:00:00Z"}]},'
               '"other_clis":[]}')
    reply = slash.dispatch("/usage", _ctx(run=_usage_run(payload)))
    blob = _all_markdown(reply)
    assert "🟧 Kimi" in blob
    assert "剩余 80%" in blob
    # 80% remaining → green
    assert "color='green'" in blob


def test_usage_card_marks_header_red_when_codex_or_kimi_failed():
    """R170: any of the per-CLI probes failing flips header to red so
    the boss spots a broken cred from the chat title."""
    payload = ('{"view":"daily","claude_code":null,'
               '"codex":{"ok":false,"note":"auth.json not found"},'
               '"kimi":null,"other_clis":[]}')
    reply = slash.dispatch("/usage", _ctx(run=_usage_run(payload)))
    assert reply["header"]["template"] == "red"


def test_usage_card_handles_invalid_json_gracefully():
    """Shell-out returned non-JSON (e.g. claudeteam usage crashed) →
    fall back to empty data; render the no-data placeholder + footer
    instead of crashing."""
    bad_run = lambda argv, **kw: type("R", (), {
        "returncode": 0, "stdout": "not json {[", "stderr": ""})()
    reply = slash.dispatch("/usage", _ctx(run=bad_run))
    assert isinstance(reply, dict)
    contents = " ".join(e.get("content", "") for e in _elements(reply)
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
    body = _body_text(reply)
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


def test_compact_detects_llm_rejection_marker_and_skips_reidentify():
    """Claude 2.x refuses programmatically-injected slash commands with
    'It can't be triggered from inside a response'. The handler should
    peek the pane after inject and surface that rejection instead of
    optimistically claiming success + scheduling a useless reidentify.
    Caught 2026-05-07 host smoke."""
    scheduled = []

    def fake_inject(target, text, **kw):
        return True

    def fake_capture(target, *, lines=80):
        return ("⏺ /compact is a built-in CLI command — please run it "
                "yourself in the terminal.\n  It can't be triggered from "
                "inside a response.")

    def capture_bg(fn):
        scheduled.append(fn)

    with tmux_patch(inject=fake_inject, capture_pane=fake_capture):
        reply = slash.dispatch("/compact worker_cc", _ctx(background=capture_bg))
    assert scheduled == [], "no reidentify should be scheduled when LLM rejected /compact"
    assert "⚠️" in reply
    assert "claude" in reply.lower() or "/clear" in reply
    assert "已让 agent 自压缩上下文" not in reply, \
        "must not falsely claim compact succeeded"


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
