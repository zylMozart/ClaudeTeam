"""Tests for feishu/slash.py — router-level slash command dispatch."""
from __future__ import annotations

from helpers import attr_patch, isolated_env, tmux_patch
from claudeteam.feishu import slash
from claudeteam.runtime import tmux
from claudeteam.runtime import pane_probe
from claudeteam.runtime import teamctl as _teamctl


def _probe_states(states=None, default=pane_probe.IDLE):
    """Patch the marker-free batched probe (`probe_many`, what /team now
    calls) to a per-window state map. /team no longer scrapes pane text."""
    states = states or {}
    return attr_patch(pane_probe,
                      probe_many=lambda targets, **kw: {
                          t: states.get(t.window, default) for t in targets})


def _elements(reply):
    """Card-shape adapter. Both simple_card and rich_card return v2
    (`body.elements`). Helper kept for legacy tests + future-proofing if
    we ever flip a builder back to v1."""
    if "elements" in reply:
        return reply["elements"]
    return reply.get("body", {}).get("elements", [])


def _all_markdown(reply) -> str:
    """Concatenate every `tag: markdown` element's content. column_set
    was dropped, so all card body content lives in plain markdown
    elements — this helper lets tests assert on text substrings without
    caring about element ordering or layout."""
    return "\n".join(e.get("content", "") for e in _elements(reply)
                     if e.get("tag") == "markdown")


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


def _team_env():
    """isolated_env preloaded with the same 3-agent team `_ctx()` defaults
    to. Handlers resolve agents live from config via `_live_agents()` (not
    ctx.team_agents), so any test that dispatches against worker_cc /
    worker_codex must pin a real team file — without this, the suite
    accidentally reads whatever claudeteam.toml sits at the repo root and
    the handler replies 未知 agent instead of touching the pane."""
    from helpers import isolated_env
    return isolated_env(team={"session": "ClaudeTeam", "agents": {
        "manager": {"cli": "claude-code", "runner": "tmux"},
        "worker_cc": {"cli": "claude-code", "runner": "tmux"},
        "worker_codex": {"cli": "codex-cli", "runner": "tmux"},
    }})


# ── /help ────────────────────────────────────────────────────────


# ── /team ────────────────────────────────────────────────────────


def test_team_classifies_each_pane_state_with_emoji():
    """REGRESSION: /team groups each agent by marker-free probe state +
    brief. Returns a Feishu card; check the body element for the
    emoji+name+brief lines and the tally summary footer."""
    states = {
        "manager": pane_probe.IDLE,       # → 💤 idle
        "worker_cc": pane_probe.BUSY,     # → 🔄 working
        "worker_codex": pane_probe.DEAD,  # → 🛑 CLI down
    }
    with _team_env(), _probe_states(states):
        reply = slash.dispatch("/team",
                               _ctx(agents=("manager", "worker_cc", "worker_codex")))

    assert isinstance(reply, dict)
    title = reply["header"]["title"]["content"]
    assert "/team" in title and "员工实时状态" in title
    body = reply["body"]["elements"][0]["content"]
    assert "💤" in body and "manager" in body         # idle (alive, settled)
    assert "🔄" in body and "worker_cc" in body       # working (pane moving)
    assert "🛑" in body and "worker_codex" in body    # CLI down (shell)
    assert "3 agents" in body


def test_team_card_reflects_live_toml_after_adding_agent():
    """REGRESSION: previously /team handler used ctx.team_agents +
    ctx.lazy_agents pre-computed at router startup, so editing
    claudeteam.toml to add a new agent did NOT show up until restart.
    A config file is meant to live-edit. Now /team re-reads team config
    every call."""
    from helpers import isolated_env
    # Initial config: 2 agents
    team = {"session": "ClaudeTeam", "agents": {
        "manager":   {"cli": "claude-code"},
        "worker_cc": {"cli": "claude-code"},
    }}
    with isolated_env(team=team), _probe_states(default=pane_probe.IDLE):
        # ctx still has stale 2-agent list; but handler should ignore
        # ctx and re-read from disk → see exactly the 2 agents.
        reply1 = slash.dispatch("/team",
                                _ctx(agents=("manager", "worker_cc")))
        body1 = reply1["body"]["elements"][0]["content"]
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
        body2 = reply2["body"]["elements"][0]["content"]
        assert "3 agents" in body2
        assert "worker_codex" in body2


def test_team_card_drops_agent_removed_from_toml_live():
    """REGRESSION (reverse direction): removing an agent block from
    claudeteam.toml should make the next /team stop listing it.
    Without _live_agents() reading config fresh, the daemon's startup
    cache would keep showing the now-deleted agent forever."""
    from helpers import isolated_env
    from claudeteam.runtime import paths, tunables as _tun

    team = {"session": "ClaudeTeam", "agents": {
        "manager":      {"cli": "claude-code"},
        "worker_cc":    {"cli": "claude-code"},
        "worker_codex": {"cli": "codex-cli"},
    }}
    with isolated_env(team=team), _probe_states(default=pane_probe.IDLE):
        # 3 agents
        reply1 = slash.dispatch("/team",
                                _ctx(agents=("manager", "worker_cc", "worker_codex")))
        body1 = reply1["body"]["elements"][0]["content"]
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
        body2 = reply2["body"]["elements"][0]["content"]
        assert "2 agents" in body2
        assert "worker_codex" not in body2


def test_team_card_reflects_lazy_flag_added_to_toml_live():
    """Adding `lazy = true` to an agent in claudeteam.toml should
    flip its /team glyph to ⏸ immediately — no router restart.
    The team card stays green because lazy is by design."""
    from helpers import isolated_env
    from claudeteam.runtime import paths, tunables as _tun

    # manager alive/idle; worker_cc dropped to a bare shell → probe DEAD
    states = {"manager": pane_probe.IDLE, "worker_cc": pane_probe.DEAD}

    team = {"session": "ClaudeTeam", "agents": {
        "manager":   {"cli": "claude-code", "runner": "tmux"},
        "worker_cc": {"cli": "claude-code", "runner": "tmux"},
    }}
    with isolated_env(team=team), _probe_states(states):
        # Before: worker_cc is not lazy, CLI down → 🛑 → yellow team
        reply1 = slash.dispatch("/team",
                                _ctx(agents=("manager", "worker_cc")))
        assert reply1["header"]["template"] == "yellow"
        body1 = reply1["body"]["elements"][0]["content"]
        assert "🛑" in body1

        # Operator edits toml: mark worker_cc lazy
        cf = paths.config_file()
        cf.write_text(
            '[team]\nsession = "ClaudeTeam"\n\n'
            '[team.agents.manager]\ncli = "claude-code"\nrunner = "tmux"\n\n'
            '[team.agents.worker_cc]\ncli = "claude-code"\nrunner = "tmux"\nlazy = true\n',
            encoding='utf-8')
        _tun.reset_cache()

        # After: lazy flag picked up live → ⏸ glyph + green team
        reply2 = slash.dispatch("/team",
                                _ctx(agents=("manager", "worker_cc")))
        assert reply2["header"]["template"] == "green"
        body2 = reply2["body"]["elements"][0]["content"]
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
    """An agent configured `lazy: true` showing 🛑 because its CLI hasn't
    spawned yet is NOT a failure — flag it ⏸ and keep the team header
    green (guards against that false-positive)."""
    from helpers import isolated_env
    # worker_lazy never woken → bare shell → probe DEAD, but lazy = expected
    states = {"manager": pane_probe.IDLE, "worker_lazy": pane_probe.DEAD}

    team = {"session": "ClaudeTeam", "agents": {
        "manager": {"cli": "claude-code"},
        "worker_lazy": {"cli": "kimi-code", "lazy": True},
    }}
    with isolated_env(team=team), _probe_states(states):
        # lazy_agents flows in via SlashContext (the closure in
        # commands/router.py pre-computes the set at daemon startup so
        # /team's hot path doesn't read team.json). Tests pass it
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
    # worker_cc's CLI actually exited (probe DEAD) and it is NOT lazy.
    states = {"manager": pane_probe.IDLE, "worker_cc": pane_probe.DEAD}

    team = {"session": "ClaudeTeam", "agents": {
        "manager": {"cli": "claude-code", "runner": "tmux"},
        "worker_cc": {"cli": "claude-code", "runner": "tmux"},  # no lazy
    }}
    with isolated_env(team=team), _probe_states(states):
        reply = slash.dispatch("/team",
                               _ctx(agents=("manager", "worker_cc")))
    assert reply["header"]["template"] == "yellow"
    body = reply["body"]["elements"][0]["content"]
    assert "🛑" in body  # honest failure glyph kept


# ── /health (server-load card with column_set 3 grid) ────────────


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
    """/health card has 🖥️ 主机总览 with CPU + 内存 + 磁盘 metrics. An
    earlier version used `column_set 3`; column_set was dropped (Feishu's
    renderer collapsed it anyway) so cells now render as paragraph-
    separated markdown — assertions look for the label/value substrings
    rather than column structure."""
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


# ── /usage (rich card with column_set 2 + ccusage summary) ───────


def _usage_run(json_payload: str):
    """Stub `ctx.run` to return JSON of `claudeteam usage --json`."""
    return lambda argv, **kw: type("R", (), {
        "returncode": 0, "stdout": json_payload, "stderr": ""})()


def test_usage_no_view_shells_claudeteam_usage_json():
    """Handler shells out with `--json` so the card builder gets
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


def test_usage_card_renders_cc_metrics_with_traffic_light():
    """Real per-window utilization replaces ccusage Total. Each metric
    gets `**剩余 X%**` with traffic-light color (green > orange > red as
    remaining drops)."""
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
    """The extra_usage block (non-Max paid burst) renders as
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
    """Codex section surfaces real % consumed per limit window
    (5h / Weekly / etc) — not just plan + email, since plan-only output
    isn't actionable."""
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
    """Each kimi metric appears with a traffic-light remaining-percent,
    as one-line markdown rows."""
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
    """Any of the per-CLI probes failing flips header to red so the boss
    spots a broken cred from the chat title."""
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
    """/tmux returns a blue card with fenced pane body so the monospace
    pane content (spinner / box drawing / banners) renders aligned in
    Feishu."""
    captured = {"calls": []}

    def fake_capture(target, lines=80):
        captured["calls"].append((str(target), lines))
        return "line1\nline2\nline3"

    with _team_env(), tmux_patch(capture_pane=fake_capture):
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

    with _team_env(), tmux_patch(capture_pane=fake_capture):
        slash.dispatch("/tmux", _ctx(agents=("manager", "worker_cc")))
    assert captured["target"] == "ClaudeTeam:manager"


def test_tmux_clamps_lines_to_max():
    captured = {}

    def fake_capture(target, lines=80):
        captured["lines"] = lines
        return ""

    with _team_env(), tmux_patch(capture_pane=fake_capture):
        slash.dispatch("/tmux manager 99999", _ctx())
    assert captured["lines"] == 2000  # _MAX_TMUX_LINES


# ── /send ────────────────────────────────────────────────────────


def test_send_inject_into_pane():
    captured = {}

    def fake_inject(target, text, **kw):
        captured["target"] = str(target)
        captured["text"] = text
        return True

    with _team_env(), tmux_patch(inject=fake_inject):
        reply = slash.dispatch("/send worker_cc hello world", _ctx())
    assert captured["target"] == "ClaudeTeam:worker_cc"
    assert captured["text"] == "hello world"
    assert "✅" in reply


def test_send_unknown_agent_warns():
    reply = slash.dispatch("/send ghost yo", _ctx())
    assert "未知 agent" in reply


# ── /compact ─────────────────────────────────────────────────────


def test_compact_injects_literal_compact_into_pane():
    captured = []

    def fake_inject(target, text, **kw):
        captured.append((str(target), text))
        return True

    with _team_env(), tmux_patch(inject=fake_inject):
        reply = slash.dispatch("/compact worker_cc", _ctx())
    assert ("ClaudeTeam:worker_cc", "/compact") in captured
    # Default ctx has background=no-op so no second inject for reidentify
    assert len(captured) == 1
    assert "45s 后自动重注 identity" in reply


def test_compact_schedules_background_reidentify_on_success():
    """/compact should schedule a delayed re-injection of the identity
    init prompt so the agent reloads identity.md after its self-compact
    settles."""
    captured = []
    scheduled = []

    def fake_inject(target, text, **kw):
        captured.append((str(target), text))
        return True

    def capture_bg(fn):
        scheduled.append(fn)

    with _team_env(), tmux_patch(inject=fake_inject):
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

    with _team_env(), tmux_patch(inject=fake_inject):
        reply = slash.dispatch("/compact worker_cc", _ctx(background=capture_bg))
    assert scheduled == []
    assert "45s 后自动重注 identity" not in reply


def test_compact_detects_llm_rejection_marker_and_skips_reidentify():
    """Claude 2.x refuses programmatically-injected slash commands with
    'It can't be triggered from inside a response'. The handler should
    peek the pane after inject and surface that rejection instead of
    optimistically claiming success + scheduling a useless reidentify."""
    scheduled = []

    def fake_inject(target, text, **kw):
        return True

    def fake_capture(target, *, lines=80):
        return ("⏺ /compact is a built-in CLI command — please run it "
                "yourself in the terminal.\n  It can't be triggered from "
                "inside a response.")

    def capture_bg(fn):
        scheduled.append(fn)

    with _team_env(), tmux_patch(inject=fake_inject, capture_pane=fake_capture):
        reply = slash.dispatch("/compact worker_cc", _ctx(background=capture_bg))
    assert scheduled == [], "no reidentify should be scheduled when LLM rejected /compact"
    assert "⚠️" in reply
    assert "claude" in reply.lower() or "/clear" in reply
    assert "已让 agent 自压缩上下文" not in reply, \
        "must not falsely claim compact succeeded"


# ── /stop ────────────────────────────────────────────────────────


def test_stop_sends_interrupt_key_escape_not_ctrl_c():
    captured = {}

    def fake_send_keys(target, *keys, **kw):
        captured["target"] = str(target)
        captured["keys"] = keys
        return True

    with _team_env(), tmux_patch(send_keys=fake_send_keys):
        reply = slash.dispatch("/stop worker_cc", _ctx())
    assert captured["target"] == "ClaudeTeam:worker_cc"
    assert "Escape" in captured["keys"]        # interrupt key is Esc now
    assert "C-c" not in captured["keys"]        # NOT the old (unsafe) Ctrl-C
    assert "Esc" in reply


def test_stop_codex_also_uses_escape():
    """All adapters unify on Esc — codex (which overrides submit_keys) still
    interrupts with Esc, not a per-CLI special sequence."""
    calls = []

    def fake_send_keys(target, *keys, **kw):
        calls.append((str(target), keys))
        return True

    with _team_env(), tmux_patch(send_keys=fake_send_keys):
        slash.dispatch("/stop worker_codex", _ctx())
    assert calls == [("ClaudeTeam:worker_codex", ("Escape",))]


def test_stop_no_args_stops_all_agents():
    """/stop with no arg fans the interrupt out to EVERY agent (the
    `/stop all` default the boss asked for), not an error."""
    targets = []

    def fake_send_keys(target, *keys, **kw):
        targets.append(str(target))
        return True

    with _team_env(), tmux_patch(send_keys=fake_send_keys):
        reply = slash.dispatch("/stop", _ctx())
    assert set(targets) == {
        "ClaudeTeam:manager", "ClaudeTeam:worker_cc", "ClaudeTeam:worker_codex"}
    assert "全员" in reply
    assert "3/3" in reply


# ── /clear ───────────────────────────────────────────────────────


def test_clear_injects_clear_then_init_prompt():
    sequence = []
    frames = {"n": 0}

    def fake_inject(target, text, **kw):
        sequence.append((str(target), text))
        return True

    def moving(target, lines=80):
        frames["n"] += 1
        return f"frame{frames['n']}"        # pane keeps moving → submit confirmed

    with _team_env(), tmux_patch(inject=fake_inject, capture_pane=moving):
        reply = slash.dispatch("/clear worker_cc", _ctx())
    # First inject: literal /clear
    assert sequence[0] == ("ClaudeTeam:worker_cc", "/clear")
    # Second inject (via inject_and_confirm): identity init prompt
    assert sequence[1][0] == "ClaudeTeam:worker_cc"
    assert "worker_cc" in sequence[1][1]
    assert "agents/worker_cc/identity.md" in sequence[1][1]
    assert "✅" in reply


# ── /clear + /compact are CLI-aware (per-adapter command, not hardcoded) ──


def test_clear_uses_clear_for_codex_too():
    """Every CLI exposes /clear — codex must get it injected (the adapter
    declares the command), not be punted to /restart."""
    sequence = []
    frames = {"n": 0}

    def fake_inject(target, text, **kw):
        sequence.append((str(target), text))
        return True

    def moving(target, lines=80):
        frames["n"] += 1
        return f"frame{frames['n']}"        # pane keeps moving → submit confirmed

    with _team_env(), tmux_patch(inject=fake_inject, capture_pane=moving):
        reply = slash.dispatch("/clear worker_codex", _ctx())
    assert sequence[0] == ("ClaudeTeam:worker_codex", "/clear")
    assert "✅" in reply


def test_compact_uses_compress_for_gemini():
    """gemini/qwen compact via /compress — the handler injects the adapter's
    command, proving it's per-CLI rather than a hardcoded '/compact'."""
    from helpers import isolated_env
    captured = []

    def fake_inject(target, text, **kw):
        captured.append((str(target), text))
        return True

    team = {"session": "ClaudeTeam",
            "agents": {"worker_gem": {"cli": "gemini-cli"}}}
    with isolated_env(team=team), tmux_patch(inject=fake_inject):
        reply = slash.dispatch("/compact worker_gem",
                               _ctx(agents=("worker_gem",)))
    assert ("ClaudeTeam:worker_gem", "/compress") in captured
    assert "/compress" in reply


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
    with _team_env(), tmux_patch(capture_pane=boom_capture):
        reply = slash.dispatch("/team", _ctx())
    # /team's per-agent capture has its own try/except → falls back to
    # empty buffer → tally still works. Use /tmux to exercise the
    # outer dispatch error path instead, since it doesn't catch internally.
    # …actually /tmux's tmux.capture_pane call is unguarded; dispatch
    # outer catch should land it.
    with _team_env(), tmux_patch(capture_pane=boom_capture):
        reply = slash.dispatch("/tmux manager", _ctx())
    assert "slash handler error" in reply or "kaboom" in reply


# ── /task ─────────────────────────────────────────────────────────


def _seed_kanban():
    """Seed a small task store covering several statuses + an intent
    back-link, for the /task render tests. Returns nothing; callers
    dispatch /task and assert on the card body."""
    from claudeteam.store import tasks
    iid = tasks.create_intent("把支付页改成两步结账 [I-SEED]")
    tasks.create("worker_cc", "重构结账", intent_id=iid)        # T-1 待处理
    tasks.create("worker_cc", "改首页")                          # T-2 待处理
    tasks.update("T-2", status="进行中")
    tasks.create("worker_codex", "写测试")                       # T-3
    tasks.update("T-3", status="进行中")
    tasks.pause("T-3")                                           # T-3 需审批
    tasks.create("worker_cc", "老活")                            # T-4
    tasks.update("T-4", status="已完成")


def test_task_kanban_groups_by_status_with_intent_backlink():
    """/task renders every status column, lists id+title+assignee, and
    carries the verbatim intent back-link (↳ I-n) when a task has one."""
    from helpers import isolated_env
    team = {"agents": {"worker_cc": {"cli": "claude-code"},
                       "worker_codex": {"cli": "codex"}}}
    with isolated_env(team=team):
        _seed_kanban()
        reply = slash.dispatch("/task", _ctx())
    assert isinstance(reply, dict)
    title = reply["header"]["title"]["content"]
    assert "/task" in title and "任务看板" in title
    body = reply["body"]["elements"][0]["content"]
    # every column header present, in vocabulary
    for col in ("待处理", "进行中", "需审批", "已完成", "已取消"):
        assert col in body
    # ids + titles + assignees surfaced
    assert "T-1" in body and "重构结账" in body and "worker_cc" in body
    assert "T-3" in body and "worker_codex" in body
    # intent back-link rendered for the linked task only
    assert "↳ I-1" in body
    assert "I-SEED" in body                       # verbatim snippet carried


def test_task_kanban_empty_columns_marked_none():
    """With no tasks the card still renders all columns, each marked 无,
    and stays green (nothing awaiting approval)."""
    from helpers import isolated_env
    with isolated_env(team={"agents": {"worker_cc": {"cli": "claude-code"}}}):
        reply = slash.dispatch("/task", _ctx())
    assert isinstance(reply, dict)
    body = reply["body"]["elements"][0]["content"]
    assert "暂无任何 task" in body
    assert reply["header"]["template"] == "green"


def test_task_card_turns_yellow_when_pending_approval():
    """A task sitting in 需审批 paints the card yellow so the boss notices
    something awaits their decision."""
    from helpers import isolated_env
    from claudeteam.store import tasks
    with isolated_env(team={"agents": {"worker_cc": {"cli": "claude-code"}}}):
        tasks.create("worker_cc", "需要拍板的活")
        tasks.update("T-1", status="进行中")
        tasks.pause("T-1")
        reply = slash.dispatch("/task", _ctx())
    assert reply["header"]["template"] == "yellow"


def test_task_handler_never_writes_to_store():
    """/task is read-only: dispatching it must not mutate tasks.json."""
    from helpers import isolated_env
    from claudeteam.store import tasks
    with isolated_env(team={"agents": {"worker_cc": {"cli": "claude-code"}}}):
        tasks.create("worker_cc", "原样不动")
        before = tasks.list_tasks()
        slash.dispatch("/task", _ctx())
        after = tasks.list_tasks()
    assert before == after


def test_task_kanban_folds_terminal_columns_by_default():
    """Don't flood the kanban with finished entries. By default
    已完成/已取消 render header+count only (▸ 已折叠), their item
    rows stay hidden, and a footer hint names `/task all` as the expand.
    Active columns keep full detail."""
    from helpers import isolated_env
    from claudeteam.store import tasks
    team = {"agents": {"worker_cc": {"cli": "claude-code"},
                       "worker_codex": {"cli": "codex"}}}
    with isolated_env(team=team):
        _seed_kanban()
        tasks.create("worker_cc", "废弃活")                # T-5
        tasks.update("T-5", status="已取消")
        reply = slash.dispatch("/task", _ctx())
    body = reply["body"]["elements"][0]["content"]
    # terminal columns: count survives, detail rows don't
    assert "已完成**（1）▸ 已折叠" in body
    assert "已取消**（1）▸ 已折叠" in body
    assert "老活" not in body and "T-4" not in body
    assert "废弃活" not in body and "T-5" not in body
    # active columns keep full detail
    assert "重构结账" in body and "写测试" in body
    # expand affordance is named
    assert "/task all" in body


def test_task_all_expands_terminal_columns():
    """`/task all` (and `/task 全部`) shows terminal item rows and drops
    the fold hint — full historical view on demand."""
    from helpers import isolated_env
    team = {"agents": {"worker_cc": {"cli": "claude-code"},
                       "worker_codex": {"cli": "codex"}}}
    with isolated_env(team=team):
        _seed_kanban()
        reply = slash.dispatch("/task all", _ctx())
        reply_cn = slash.dispatch("/task 全部", _ctx())
    for r in (reply, reply_cn):
        body = r["body"]["elements"][0]["content"]
        assert "老活" in body and "T-4" in body
        assert "已折叠" not in body


def test_task_empty_terminal_columns_render_none_not_fold():
    """An EMPTY terminal column has nothing to hide — it renders the
    plain 无 marker, not a fold line (folding zero rows would imply
    hidden content that doesn't exist)."""
    from helpers import isolated_env
    from claudeteam.store import tasks
    with isolated_env(team={"agents": {"worker_cc": {"cli": "claude-code"}}}):
        tasks.create("worker_cc", "唯一的活")               # 待处理 only
        reply = slash.dispatch("/task", _ctx())
    body = reply["body"]["elements"][0]["content"]
    assert "已折叠" not in body
    assert body.count("　无") >= 2     # 已完成 + 已取消 both plain-empty


# ── team control: /shutdown /restart (lifecycle gate) ──────────────


def test_shutdown_refused_when_lifecycle_disabled():
    spawned = []
    with _team_env(), attr_patch(_teamctl, lifecycle_slash_enabled=lambda: False,
                                 spawn_detached=lambda a: spawned.append(a)):
        reply = slash.dispatch("/shutdown", _ctx())
    assert "未开启" in _all_markdown(reply)
    assert spawned == []          # default-deny: nothing torn down


def test_shutdown_requires_confirmation():
    spawned = []
    with _team_env(), attr_patch(_teamctl, lifecycle_slash_enabled=lambda: True,
                                 spawn_detached=lambda a: spawned.append(a)):
        reply = slash.dispatch("/shutdown", _ctx())
    assert "确认" in _all_markdown(reply)
    assert spawned == []          # bare /shutdown only previews, never tears down


def test_shutdown_confirm_spawns_detached_runner():
    spawned = []
    with _team_env(), attr_patch(_teamctl, lifecycle_slash_enabled=lambda: True,
                                 spawn_detached=lambda a: spawned.append(a)):
        reply = slash.dispatch("/shutdown 确认", _ctx())
    assert spawned == [["team-shutdown"]]
    assert "detached" in _all_markdown(reply)


def test_restart_refused_when_lifecycle_disabled():
    spawned = []
    with _team_env(), attr_patch(_teamctl, lifecycle_slash_enabled=lambda: False,
                                 spawn_detached=lambda a: spawned.append(a)):
        reply = slash.dispatch("/restart", _ctx())
    assert "未开启" in _all_markdown(reply)
    assert spawned == []


def test_restart_direct_spawns_detached_runner():
    """/restart is recoverable → no confirm step; fires immediately."""
    spawned = []
    with _team_env(), attr_patch(_teamctl, lifecycle_slash_enabled=lambda: True,
                                 spawn_detached=lambda a: spawned.append(a)):
        reply = slash.dispatch("/restart", _ctx())
    assert spawned == [["team-restart"]]
    assert "detached" in _all_markdown(reply)


# ── team control: /login (login gate, independent of lifecycle) ────


def test_login_refused_when_login_disabled():
    with _team_env(), attr_patch(_teamctl, login_slash_enabled=lambda: False):
        reply = slash.dispatch("/login cc", _ctx())
    assert "未开启" in _all_markdown(reply)


def test_login_gate_is_independent_of_lifecycle_gate():
    """Lifecycle ON but login OFF → /login still refused. The two flags are
    separate so /shutdown can be demoed while /login stays inert until creds
    are isolated."""
    with _team_env(), attr_patch(_teamctl, lifecycle_slash_enabled=lambda: True,
                                 login_slash_enabled=lambda: False):
        reply = slash.dispatch("/login cc", _ctx())
    assert "未开启" in _all_markdown(reply)


def test_login_unknown_cli():
    with _team_env(), attr_patch(_teamctl, login_slash_enabled=lambda: True):
        reply = slash.dispatch("/login frobnicate", _ctx())
    assert "不认识" in _all_markdown(reply)


def test_login_ambiguous_cli_requires_agent():
    # _team_env has manager + worker_cc both on claude-code → must disambiguate
    with _team_env(), attr_patch(_teamctl, login_slash_enabled=lambda: True):
        reply = slash.dispatch("/login cc", _ctx())
    assert "多个 agent" in _all_markdown(reply)


def _real_sleep_ctx():
    import time as _t
    return _ctx(sleep=lambda s: _t.sleep(0.03))


def test_login_triggers_subprocess_immediate_ack_zero_llm():
    """ZERO-LLM path: /login returns an immediate ack and schedules the
    router-driven subprocess in the background (NOT injected into the agent
    pane). The ack must advertise the pure-mechanical, model-down-safe path."""
    scheduled = []
    with _team_env(), attr_patch(_teamctl, login_slash_enabled=lambda: True):
        reply = slash.dispatch("/login codex",
                               _ctx(background=lambda fn: scheduled.append(fn)))
    md = _all_markdown(reply)
    assert "不经 agent 大模型" in md           # zero-LLM guarantee (body)
    assert "codex login --device-auth" in md  # router runs it directly
    assert len(scheduled) == 1                # background subprocess scheduled


def test_login_interactive_cli_returns_pane_guidance_path_b():
    """Interactive (stdin-paste) CLIs like claude take path B: no fragile
    chat relay — guide the operator to complete in the pane (still zero-LLM,
    no router subprocess spawned)."""
    scheduled = []
    with _team_env(), attr_patch(_teamctl, login_slash_enabled=lambda: True):
        reply = slash.dispatch("/login cc worker_cc",
                               _ctx(background=lambda fn: scheduled.append(fn)))
    md = _all_markdown(reply)
    assert "pane 里直接跑" in md and "claude auth login" in md
    assert "不经大模型" in md                  # zero-LLM guarantee preserved
    assert scheduled == []                     # NO subprocess for interactive path


def test_login_run_subprocess_captures_stdout_surface():
    """The runner spawns the login command directly and scrapes its STDOUT
    (no agent, no LLM) — codex device-auth shape."""
    import os
    posted = []
    argv = ["sh", "-c",
            "printf 'Open: https://auth.openai.com/codex/device\\n"
            "one-time code: MMLU-W452Y\\n'"]
    with attr_patch(slash, _login_post_card=lambda t, b, *, color: posted.append((t, b, color))):
        slash._login_run_subprocess(_real_sleep_ctx(), argv, os.environ.copy(),
                                    "codex", "worker_codex", "codex-cli")
    assert len(posted) == 1
    _, body, color = posted[0]
    assert "auth.openai.com/codex/device" in body
    assert "MMLU-W452Y" in body
    assert color == "blue"


def test_login_run_subprocess_exit_without_surface():
    """A login subprocess that exits with no verification surface → a
    'no code' card (already logged in / errored), not an infinite poll."""
    import os
    posted = []
    with attr_patch(slash, _login_post_card=lambda t, b, *, color: posted.append((t, b, color))):
        slash._login_run_subprocess(_real_sleep_ctx(), ["sh", "-c", "exit 0"],
                                    os.environ.copy(), "codex", "worker_codex", "codex-cli")
    assert len(posted) == 1
    assert "无验证码" in posted[0][0] and posted[0][2] == "yellow"


def test_login_run_subprocess_spawn_failure():
    """A non-executable login binary → an honest failure card, never a crash."""
    import os
    posted = []
    with attr_patch(slash, _login_post_card=lambda t, b, *, color: posted.append((t, b, color))):
        slash._login_run_subprocess(_real_sleep_ctx(), ["ct-no-such-binary-zzz"],
                                    os.environ.copy(), "x", "worker_codex", "codex-cli")
    assert len(posted) == 1
    assert "失败" in posted[0][1] and posted[0][2] == "red"


def test_login_env_isolates_codex_and_claude_homes():
    """The subprocess env points the CLI's token write at the agent's
    ISOLATED per-agent home, never the host's."""
    from claudeteam.runtime.paths import agent_home
    with _team_env():
        ce = slash._login_env("codex-cli", "worker_codex")
        assert ce["CODEX_HOME"] == f"{agent_home('worker_codex')}/.codex"
        he = slash._login_env("claude-code", "manager")
        assert he["HOME"] == agent_home("manager")


def test_login_kimi_hard_refused_not_isolated():
    """kimi shares ~/.kimi (not host-isolated) → /login HARD-REFUSED, never
    injected, so it can't clobber the operator's host kimi creds."""
    team = {"session": "ClaudeTeam", "agents": {
        "manager": {"cli": "claude-code"},
        "worker_kimi": {"cli": "kimi-code"}}}
    injected = []
    with isolated_env(team=team), \
            attr_patch(_teamctl, login_slash_enabled=lambda: True), \
            tmux_patch(inject=lambda *a, **kw: injected.append(a) or True,
                       capture_pane=lambda *a, **kw: ""):
        reply = slash.dispatch("/login kimi",
                               _ctx(agents=("manager", "worker_kimi")))
    md = _all_markdown(reply)
    assert "未启用" in md or "硬拒" in md
    assert injected == []          # never touched the pane / host creds


def test_login_cli_allowed_when_in_allowlist_tunable():
    """A CLI added to controls.login_allowed_clis is no longer refused."""
    from helpers import env_patch
    from claudeteam.runtime import tunables
    team = {"session": "ClaudeTeam", "agents": {
        "manager": {"cli": "claude-code"},
        "worker_kimi": {"cli": "kimi-code"}}}
    pane = "https://auth.moonshot.cn/device\ncode: ABCD-1234\n"
    with isolated_env(team=team) as tmp:
        toml = tmp / "controls.toml"
        toml.write_text("[controls]\nlogin_allowed_clis = \"claude-code,kimi-code\"\n",
                        encoding="utf-8")
        with env_patch(CLAUDETEAM_CONFIG_FILE=str(toml)):
            tunables.reset_cache()
            with attr_patch(_teamctl, login_slash_enabled=lambda: True), \
                    tmux_patch(inject=lambda *a, **kw: True,
                               capture_pane=lambda *a, **kw: pane):
                reply = slash.dispatch("/login kimi",
                                       _ctx(agents=("manager", "worker_kimi")))
    md = _all_markdown(reply)
    assert "未启用" not in md                 # allowlisted → not hard-refused
    assert "自动补一张卡" in md                # kimi = device-code → Path A (pure-chat, fire-and-forget)
    assert "pane 里直接跑" not in md           # NOT Path B (kimi isn't interactive paste-back)
    assert "共享 HOME" in md                   # shared ~/.kimi warning still shown


def test_login_no_subcommand_cli_routes_to_env_key_card():
    """gemini/qwen have no scriptable re-auth subcommand → /login surfaces the
    .env API-key route (agent_auth) instead of running a fake `auth login`."""
    from helpers import env_patch
    from claudeteam.runtime import tunables
    team = {"session": "ClaudeTeam", "agents": {
        "manager": {"cli": "claude-code"},
        "worker_g": {"cli": "gemini-cli"}}}
    with isolated_env(team=team) as tmp:
        toml = tmp / "controls.toml"
        toml.write_text("[controls]\nlogin_allowed_clis = \"claude-code,gemini-cli\"\n",
                        encoding="utf-8")
        with env_patch(CLAUDETEAM_CONFIG_FILE=str(toml)):
            tunables.reset_cache()
            with attr_patch(_teamctl, login_slash_enabled=lambda: True):
                reply = slash.dispatch("/login gemini",
                                       _ctx(agents=("manager", "worker_g")))
    md = _all_markdown(reply)
    assert "GEMINI_API_KEY" in md and ".env" in md   # routed to the .env-key card
    assert "agent_auth" in md                         # points at the agent_auth route


# ── security: the /login verification-surface scraper ──────────────


def test_extract_login_surface_pulls_url_and_code():
    text = "Visit https://auth.example.com/device to log in\nCode: ABCD-1234\n"
    s = slash._extract_login_surface(text)
    assert any("auth.example.com/device" in u for u in s["urls"])
    assert "ABCD-1234" in s["codes"]


def test_extract_login_surface_drops_token_bearing_lines():
    text = ("https://login.example.com/oauth?x=1\n"
            "access_token: sk-ABCDEF1234567890\n"
            "refresh_token=eyJhbGciOiJ\n"
            "export ANTHROPIC_API_KEY=sk-ant-xxxxxxxx\n")
    s = slash._extract_login_surface(text)
    assert any("login.example.com/oauth" in u for u in s["urls"])
    blob = " ".join(s["urls"] + s["codes"]).lower()
    assert "sk-" not in blob
    assert "eyj" not in blob
    assert "access_token" not in blob
    assert "api_key" not in blob


def test_extract_login_surface_caps_code_length():
    """A long opaque token must never be surfaced as a pairing code."""
    text = "code: ABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789\n"
    s = slash._extract_login_surface(text)
    assert s["codes"] == []


def test_extract_login_surface_drops_url_with_token_query():
    text = "https://x.com/callback?access_token=sk-secret123&code=ok\n"
    s = slash._extract_login_surface(text)
    assert s["urls"] == []          # token-bearing line skipped wholesale


def test_extract_login_surface_codex_device_auth_ignores_anchor():
    """Regression: the persistent worker-pane anchor
    [QUICKSTART-0613-1913] (pure-digit dddd-dddd) was mis-surfaced as the
    codex device code, posting prematurely and missing the real one. Real
    codes have letters → anchor ignored, real MLGX-BJYY9 + URL surfaced."""
    pane = ("worker_codex 上下文 anchor [QUICKSTART-0613-1913] ...\n"
            "Codex device login is waiting for authorization.\n"
            "Open: https://auth.openai.com/codex/device\n"
            "Enter code: MLGX-BJYY9  (expires in 15min)\n")
    s = slash._extract_login_surface(pane)
    assert "MLGX-BJYY9" in s["codes"]
    assert "0613-1913" not in s["codes"]          # anchor NOT a device code
    assert any("auth.openai.com/codex/device" in u for u in s["urls"])


def test_extract_login_surface_strips_ansi_color_codes():
    """Regression: codex wraps the device code in ANSI
    (\\x1b[94m...\\x1b[0m); without stripping, the code is missed and [0m
    residue leaks into the URL. Strip ANSI → clean URL + code."""
    pane = ("Open: \x1b[4mhttps://auth.openai.com/codex/device\x1b[0m\n"
            "Enter this one-time code: \x1b[94mMNOG-E7MBX\x1b[0m\n")
    s = slash._extract_login_surface(pane)
    assert "MNOG-E7MBX" in s["codes"]
    assert any(u == "https://auth.openai.com/codex/device" for u in s["urls"])  # no [0m residue
    assert all("\x1b" not in u and "[0m" not in u for u in s["urls"])
