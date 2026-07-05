"""Slash-command dispatch — zero-LLM router-level handlers.

When the chat receives a message starting with `/`, `feishu/router.py`
emits `Decision(Action.SLASH, text=raw_text)`. `feishu/deliver.py` calls
`dispatch(text, ctx)` here, gets a `str | dict` reply, and posts it
back to the chat — `str` via `chat.send_text`, `dict` (Feishu card
schema) via `chat.send_card`. **No worker pane is touched, no LLM runs.**

Supported commands:

    /help                              card listing every command
    /team                              card with each agent's pane state
                                       (health-color: green / yellow)
    /health                            card with `claudeteam health` output
                                       (yellow on ❌ / ⚠️)
    /usage [view]                      card wrapping `claudeteam usage`
                                       (default view = daily)
    /tmux [agent] [N]                  card with last N (default 10) pane lines
    /send <agent> <msg>                tmux send-keys + Enter into agent pane
    /compact [agent]                   inject /compact then schedule reidentify
    /stop [agent]                      interrupt agent's current action
                                       (adapter interrupt key, default Esc);
                                       no arg → stop every agent
    /clear <agent>                     /clear + re-init (rehire shape)
    /task [all]                        read-only kanban of the task store
                                       (grouped by status; intent back-link;
                                       terminal columns fold to counts unless
                                       `all`)
    /standup                           trigger one progress-report round now
                                       (periodic cadence: toml [standup])

Dispatch is a leading-token dict lookup (`/cmd args…` → handler(args, ctx))
so detection and execution share one path — no per-handler regex preamble.
Each handler receives only the part of the message after the command name.

Card-building helpers live in `feishu/cards.py`:
    simple_card(title, body, color)         shape constructor
    beijing_stamp(now) -> str               card-title timestamp suffix
                                            (callers pass `ctx.now`)
    fenced_block(text) -> str               monospace lark_md fence
"""
from __future__ import annotations

import re
import subprocess
import threading
import time
from collections import Counter
from dataclasses import dataclass
from datetime import datetime
from typing import Callable

from claudeteam.agents import identity
from claudeteam.runtime import config as _config
from claudeteam.runtime import pane_probe
from claudeteam.feishu.cards import (
    beijing_stamp, column_set_2, column_set_3, fenced_block, load_color,
    remaining_color, rich_card, simple_card,
)
from claudeteam.runtime import tmux
from claudeteam.store import local_facts, tasks
from claudeteam.util import fmt_bytes


# /team display: marker-free probe state → (emoji, brief). The probe reports
# liveness from the pane's foreground process and busy/idle from pane motion,
# so /team no longer scrapes TUI text to guess a state.
_TEAM_STATE_GLYPH = {
    pane_probe.IDLE: ("💤", "idle"),
    pane_probe.BUSY: ("🔄", "working"),
    pane_probe.DEAD: ("🛑", "CLI down"),
    pane_probe.NO_WINDOW: ("⬜", "no window"),
}


def _spawn_daemon_thread(fn: Callable[[], None]) -> None:
    """Default ctx.background — fire-and-forget a daemon thread.

    Used by /compact to schedule a delayed re-identify after the agent
    finishes its self-compact. Tests override this with a no-op so
    they don't block on the 45-second timer.
    """
    threading.Thread(target=fn, daemon=True).start()


# ── context wiring ────────────────────────────────────────────────


@dataclass(frozen=True)
class SlashContext:
    """Dependency bag handed to every handler. Keeps handlers pure-ish:
    they only touch what's in here, easy to fake in tests."""
    team_agents: list[str]
    session: str
    # Deprecated: handlers now pull live data via `_live_agents()` so
    # claudeteam.toml edits take effect without restarting the
    # router. These two fields are kept for back-compat with tests
    # constructing SlashContext directly.
    lazy_agents: frozenset[str] = frozenset()
    run: Callable = subprocess.run         # for shell-out (`claudeteam <cmd>`)
    sleep: Callable = time.sleep           # for /clear's settle delay
    now: Callable = datetime.now           # injectable clock for header timestamps
    background: Callable[[Callable[[], None]], None] = _spawn_daemon_thread

    @property
    def agent_set(self) -> frozenset[str]:
        return frozenset(self.team_agents)


_AGENT_NAME_RE = re.compile(r"[A-Za-z0-9_\-]+")
_REIDENTIFY_DELAY_S = 45.0   # rough upper bound for claude-code /compact


def _tmux_default_lines() -> int:
    """Default `/tmux` capture window (config-driven; was hardcoded 10)."""
    from claudeteam.runtime import tunables
    return int(tunables.tunable("limits.tmux_capture_default_lines", 10))


def _tmux_max_lines() -> int:
    """Hard upper bound for `/tmux N` (config-driven; was hardcoded 2000)."""
    from claudeteam.runtime import tunables
    return int(tunables.tunable("limits.tmux_capture_max_lines", 2000))


def _live_agents() -> tuple[list[str], frozenset[str], frozenset[str], dict]:
    """Return (ordered_list, name_set, lazy_set, agents_dict) read from
    config NOW. Slash handlers use this instead of ctx.team_agents /
    ctx.agent_set / ctx.lazy_agents so editing claudeteam.toml takes
    effect immediately (no router restart). One disk read per slash
    event — negligible vs the per-agent tmux subprocesses below."""
    agents_dict = _config.load_team().get("agents", {})
    names = list(agents_dict.keys())
    return (names, frozenset(names),
            frozenset(n for n, c in agents_dict.items() if c.get("lazy")),
            agents_dict)


def _default_agent(ctx: SlashContext) -> str:
    names, _, _, _ = _live_agents()
    return names[0] if names else "manager"


def _bad_agent(agent: str, ctx: SlashContext) -> str | None:
    """Return a Chinese warning string if `agent` is unknown, else None."""
    if not _AGENT_NAME_RE.fullmatch(agent):
        return f"⚠️ 非法 agent 名: `{agent}`"
    _, agent_set, _, _ = _live_agents()
    if agent not in agent_set:
        return f"⚠️ 未知 agent: `{agent}`（合法名: {sorted(agent_set)}）"
    return None


def _shell(ctx: SlashContext, argv: list[str], timeout: int = 30) -> str:
    """Run a shell command via ctx.run, return stdout (or stderr on failure)."""
    try:
        r = ctx.run(argv, capture_output=True, text=True, timeout=timeout)
    except (subprocess.TimeoutExpired, OSError) as e:
        return f"⚠️ {' '.join(argv[:2])}: {e}"
    if r.returncode != 0:
        return (r.stdout or "") + (r.stderr or "") or f"⚠️ rc={r.returncode}"
    return (r.stdout or "").rstrip() or "(empty)"


# ── individual handlers ───────────────────────────────────────────


_HELP_TEXT = """🆘 ClaudeTeam 自定义斜杠命令（零 LLM，router/hook 直拦）

/help                    → 本帮助
/team                    → 所有员工实时 tmux 状态（卡片）
/usage                   → 各 CLI 用量（claude ccusage / codex / kimi，卡片）
/health                  → 主机 + 员工资源占用（卡片）
/tmux [agent] [lines]    → capture-pane 窗口（默认 manager/10 行）
/send <agent> <msg>      → 直接注入消息到 agent 窗口
/compact [agent]         → 群聊无参=压缩 manager；带参压缩指定 agent
/stop [agent]            → 中断 agent 当前动作（送 Esc）；无参=停全部 agent
/clear <agent>           → 送 /clear + 重新入职 init_msg（相当于 rehire）
/task [all]              → 任务看板：按状态分组（只读）；已完成/已取消
                           默认折叠为计数，加 all 展开明细
/standup                 → 立即来一轮全员进度巡视汇报（定时见 toml [standup]）
/shutdown [确认]         → 下线 agent panes（保留 router/订阅/watchdog，便于 /restart 唤醒）；先回卡确认
/restart                 → 重启整队（≈down→up）；清残留后拉起，直接执行
/login <cli> [agent]     → 触发某 CLI 重新认证，群里弹验证链接/设备码
                           （需 controls.allow_* 开关；默认关闭=安全）"""


def _handle_help(args: str, ctx: SlashContext) -> dict:
    """/help → interactive card. Returning a dict (instead of str) signals
    deliver._apply_slash to send via chat.send_card. The body is the same
    text block used historically; only the wrapper changes."""
    return simple_card("🆘 ClaudeTeam 自定义斜杠命令", _HELP_TEXT)


def _handle_team(args: str, ctx: SlashContext) -> dict:
    """Capture each agent's pane, classify state, return as Feishu card.

    A card with header containing the timestamp + session, body listing
    one `**emoji agent**: brief` line per agent followed by a tally
    summary.

    Color is `green` when no agent is in a warning/down state, `yellow`
    when at least one is, so the boss can scan group chat at a glance.

    Lazy agents (config `lazy: true`) display as ⏸ instead of 🛑 —
    a not-yet-spawned CLI is intentional, not a failure, and
    shouldn't paint the team header yellow.
    """
    # _live_agents reads config every call so claudeteam.toml edits
    # take effect on the next /team without a router restart.
    team_agents, _, lazy_agents, _ = _live_agents()

    # Fired agents (status="已停止" via `claudeteam fire`) have no pane;
    # without this branch /team showed them as "🛑 CLI down" — same as a
    # crashed agent — so operators couldn't tell intentional firing from a
    # real failure. Split fired vs live, then probe all LIVE panes in ONE
    # shared interval (not N×0.4s) so /team doesn't stall the router loop.
    fired: dict[str, str] = {}
    to_probe: dict[str, tmux.Target] = {}
    acp_states: dict[str, str] = {}
    for agent in team_agents:
        status = local_facts.get_status(agent)
        if status and status.get("status") == "已停止":
            fired[agent] = (status.get("task") or status.get("note")
                            or status.get("blocker") or "已停止")
            continue
        if _config.agent_runner(agent) == "acp":
            # ACP agents: state comes from the delivery queue + host pid —
            # exact turn lifecycle, no pane scraping. A failed probe renders
            # as its own state, never as healthy-idle.
            from claudeteam.runtime import acp_host
            try:
                acp_states[agent] = acp_host.probe(agent)
            except OSError:
                acp_states[agent] = "probe_failed"
        else:
            to_probe[agent] = tmux.Target(ctx.session, agent)
    try:
        states = pane_probe.probe_many(list(to_probe.values()))
    except Exception:
        states = {}

    rows = []
    tally: Counter[str] = Counter()
    for agent in team_agents:
        if agent in fired:
            rows.append((agent, "🚫", f"已停止 ({fired[agent]})"))
            tally["🚫"] += 1
            continue
        if agent in acp_states:
            state = acp_states[agent]
            emoji, brief = _TEAM_STATE_GLYPH.get(state, ("🔘", state))
            brief += " · acp"
        else:
            state = states.get(to_probe[agent], pane_probe.NO_WINDOW)
            emoji, brief = _TEAM_STATE_GLYPH.get(state, ("🔘", state))
            # A never-woken lazy agent is a shell placeholder (probe DEAD /
            # NO_WINDOW) — normal before its first message arrives, so flip to
            # ⏸ (keeps the team-color check below green) instead of 🛑 CLI down.
            if state in (pane_probe.DEAD, pane_probe.NO_WINDOW) and agent in lazy_agents:
                emoji = "⏸"
                brief = "lazy (waiting for first message)"
        rows.append((agent, emoji, brief))
        tally[emoji] += 1

    body_lines = []
    for agent, emoji, brief in rows:
        body_lines.append(f"{emoji} **{agent}**: {brief}")
    if not rows:
        body_lines.append("_(no agents configured)_")

    total = sum(tally.values())
    summary = " / ".join(f"{k} {v}" for k, v in tally.most_common()) or "—"
    body_lines.append("")
    body_lines.append(f"**汇总**: {total} agents · {summary}")

    # Yellow if any agent looks unhappy (⚠️ awaiting permission, 🛑 CLI down,
    # ❌ etc.), green otherwise. 💤 idle / 🔄 working / ⏸ lazy / 🚫 fired
    # are intentional states — keep team header green.
    healthy_glyphs = ("💤", "🔄", "⏸", "🚫")
    color = ("green" if all(e in healthy_glyphs for e in tally)
             else "yellow")

    return simple_card(
        f"👥 /team — 员工实时状态 [{ctx.session}] {beijing_stamp(ctx.now)}",
        "\n".join(body_lines),
        color=color,
    )


def _agent_emoji(cpu: float) -> str:
    if cpu >= 80:
        return "🔥"
    if cpu >= 30:
        return "🔄"
    if cpu >= 5:
        return "⚙️"
    return "💤"


def _build_server_load_elements(data: dict) -> list:
    """Render the server-load data dict into v2 card elements.

    Layout: 🖥️ 主机总览 (CPU / 内存 / 磁盘 column_set) → 📦 容器总量 →
    👤 员工细分 Top N (per-row column_set) → 🚨 异常告警 → note footer.
    Each section ends with an `hr` divider; final `hr` is pruned
    before the note.
    """
    host = data.get("host") or {}
    cpu = host.get("cpu")
    mem = host.get("mem")
    disk = host.get("disk")
    containers = data.get("containers") or []
    agents = data.get("agents") or []
    alarms = data.get("alarms") or []

    elements: list = []

    # 🖥️ 主机总览 — CPU / 内存 / 磁盘
    cpu_cell = ("**CPU**\n<font color='grey'>无数据</font>" if not cpu else
                f"**CPU**\n<font color='{load_color(cpu['pct'])}'>"
                f"**{cpu['load'][0]:.2f} / {cpu['cores']} 核 ({cpu['pct']}%)**"
                f"</font>\n<font color='grey'>5m {cpu['load'][1]:.2f} · "
                f"15m {cpu['load'][2]:.2f}</font>")
    mem_cell = ("**内存**\n<font color='grey'>无数据</font>" if not mem else
                f"**内存**\n<font color='{load_color(mem['pct'])}'>"
                f"**{fmt_bytes(mem['used'])} / {fmt_bytes(mem['total'])}"
                f" ({mem['pct']}%)**</font>\n<font color='grey'>"
                f"可用 {fmt_bytes(mem['available'])} · Swap "
                f"{fmt_bytes(mem['swap']['used'])}/{fmt_bytes(mem['swap']['total'])}"
                f"</font>")
    disk_cell = ("**磁盘**\n<font color='grey'>无数据</font>" if not disk else
                 f"**磁盘** `{disk['mount']}`\n"
                 f"<font color='{load_color(disk['pct'])}'>"
                 f"**{fmt_bytes(disk['used'])} / {fmt_bytes(disk['total'])}"
                 f" ({disk['pct']}%)**</font>")
    elements.append({"tag": "markdown", "content": "**🖥️ 主机总览**"})
    elements.append(column_set_3([cpu_cell, mem_cell, disk_cell]))
    elements.append({"tag": "hr"})

    # 📦 团队容器总量
    if containers:
        running = sum(1 for c in containers if c["status"])
        total_cpu = sum(c["cpu_pct"] for c in containers)
        total_mem = sum(c["mem_used"] for c in containers)
        peak = max(containers, key=lambda c: c["mem_pct"])
        name_preview = " · ".join(c["short"] for c in containers[:3])
        if len(containers) > 3:
            name_preview += " …"
        elements.append({"tag": "markdown",
                          "content": "**📦 团队容器总量**"})
        elements.append(column_set_3([
            f"**容器数**\n**{running} / {len(containers)}** 运行中\n"
            f"<font color='grey'>{name_preview}</font>",
            f"**容器 CPU 合计**\n<font color='{load_color(int(total_cpu))}'>"
            f"**{total_cpu:.1f}%**</font>\n<font color='grey'>"
            f"跨 {len(containers)} 容器加总</font>",
            f"**容器内存合计**\n**{fmt_bytes(total_mem)}**\n"
            f"<font color='{load_color(int(peak['mem_pct']))}'>"
            f"峰值 `{peak['short']}` {peak['mem_pct']:.1f}%</font>",
        ]))
        elements.append({"tag": "hr"})

    # 👤 员工细分 Top N
    if agents:
        topn = agents[:9]
        elements.append({"tag": "markdown",
                          "content": (f"**👤 员工细分 Top {len(topn)}"
                                       f" / 共 {len(agents)}**"
                                       f"（按 CPU 降序）")})
        for i in range(0, len(topn), 3):
            row_cells = [
                f"{_agent_emoji(a['cpu'])} **{a['agent']}**\n"
                f"CPU `{a['cpu']:.1f}%` · Mem `{fmt_bytes(a['mem'])}`\n"
                f"<font color='grey'>{a['location']}</font>"
                for a in topn[i:i + 3]
            ]
            elements.append(column_set_3(row_cells))
        if len(agents) > 9:
            elements.append({"tag": "markdown",
                              "content": (f"<font color='grey'>… 另有 "
                                           f"{len(agents) - 9} 个员工未显示"
                                           f"</font>")})
        elements.append({"tag": "hr"})

    # 🚨 异常告警
    if alarms:
        body = "\n".join(f"- <font color='red'>⚠️ {a}</font>" for a in alarms)
        elements.append({"tag": "markdown",
                          "content": f"**🚨 异常告警**\n{body}"})
        elements.append({"tag": "hr"})

    # Drop the trailing hr before the note footer
    if elements and elements[-1].get("tag") == "hr":
        elements.pop()

    return elements


def _handle_health(args: str, ctx: SlashContext) -> dict:
    """Server-load snapshot card.

    Renders host CPU/mem/disk + docker containers + per-agent process
    subtree in column_set grids with red/orange/green percentages.
    Data comes from `runtime/server_metrics.collect_server_load`.
    Falls back to a "无数据" cell when the underlying `uptime` /
    `free` / `df` / `docker stats` / `ps` shell-out returns nothing
    — common inside the container on macOS Docker Desktop.
    """
    from claudeteam.runtime import server_metrics
    _, agent_set, _, _ = _live_agents()
    data = server_metrics.collect_server_load(
        agent_set=agent_set,
        session=ctx.session,
    )
    elements = _build_server_load_elements(data)
    # Lark v2 schema doesn't support the `note` element; use a
    # grey-font markdown line instead so the footer still reads as
    # subdued metadata.
    elements.append({"tag": "markdown",
                      "content": (f"<font color='grey'>采集 {beijing_stamp(ctx.now)}"
                                   f" · 数据源 uptime/free/df/docker stats/ps"
                                   f"</font>")})
    # Yellow when alarms exist, otherwise purple.
    color = "yellow" if data.get("alarms") else "purple"
    return rich_card(
        f"🩺 /health — 服务器负载 [{ctx.session}] {beijing_stamp(ctx.now)}",
        elements,
        color=color,
    )


def _usage_section(*, heading: str, ok: bool, fail_text: str,
                   plan_text: str | None, metrics: list,
                   no_metrics_note: str | None,
                   format_metric) -> list:
    """Render one CLI's section in the /usage card.

    Pulled out of `_codex_section` / `_kimi_section` because both
    follow the same shape: heading → status-on-fail → optional plan
    → optional no-metrics note → per-metric row. The differences are
    just text strings; `format_metric` is a callable that turns one
    metric dict into the right side of a `column_set_2` row.
    """
    rows: list = [{"tag": "markdown", "content": heading}]
    if not ok:
        rows.append(column_set_2(
            "**Status**", f"<font color='red'>{fail_text}</font>"))
        return rows
    if plan_text:
        rows.append(column_set_2("**Plan**", plan_text))
    if not metrics:
        if no_metrics_note:
            rows.append(column_set_2(
                "**Status**", f"<font color='grey'>{no_metrics_note}</font>"))
        return rows
    for m in metrics:
        rows.append(column_set_2(f"**{m['label']}**", format_metric(m)))
    return rows


def _codex_section(cx: dict) -> list:
    """Render Codex section rows for `/usage` card."""
    plan = cx.get("plan") or "unknown"
    def fmt(m):
        color = remaining_color(m["remaining_pct"])
        return (f"<font color='{color}'>**剩余 {m['remaining_pct']}%**</font> "
                f"· 已用 {m['used_pct']}% · 重置 {m.get('reset', '')}")
    # When the upstream probe (codex-cli-usage) isn't installed we fall
    # back to ok=True with empty metrics + a `note` summarising the
    # auth.json login status. Surface that note in the no-metrics slot
    # so the user sees "已登录 · 计划 Pro" instead of the generic
    # "codex-cli-usage 跑通但没返回 %" stub.
    return _usage_section(
        heading="**🟦 Codex (ChatGPT OAuth)**",
        ok=bool(cx.get("ok")),
        fail_text=f"Codex 用量读取失败</font> · {cx.get('note', '')}",
        plan_text=f"<font color='blue'>**{plan}**</font>" if cx.get("ok") else None,
        metrics=cx.get("metrics") or [],
        no_metrics_note=(cx.get("note")
                         or "codex-cli-usage 跑通但没返回 % 数据"),
        format_metric=fmt,
    )


def _kimi_section(km: dict) -> list:
    """Render Kimi section rows for `/usage` card."""
    def fmt(m):
        color = remaining_color(m["remaining_pct"])
        return (f"<font color='{color}'>**剩余 {m['remaining_pct']}%**</font> "
                f"· 已用 {m['used_pct']}% ({m['used']}/{m['limit']}) "
                f"· 重置 {m.get('reset_iso', '')}")
    return _usage_section(
        heading="**🟧 Kimi (api.kimi.com)**",
        ok=bool(km.get("ok")),
        fail_text=f"Kimi API 失败</font> · {km.get('note', '')}",
        plan_text=None,
        metrics=km.get("metrics") or [],
        no_metrics_note=None,
        format_metric=fmt,
    )
    return rows


def _handle_usage(args: str, ctx: SlashContext) -> dict:
    """`/usage [view]` — token / credit consumption snapshot card.

    Per-CLI sections (Claude Code via ccusage, Codex via
    `codex-cli-usage`, Kimi via `api.kimi.com/coding/v1/usages`)
    each render as `**heading**` + column_set 2 rows + hr separator.
    Per-CLI failures show as a red `Status` line in their section
    rather than failing the whole card.
    """
    view_arg = args.strip().split()[0] if args.strip() else ""
    argv = ["claudeteam", "usage", "--json"]
    if view_arg:
        argv += ["--view", view_arg]
    view = view_arg or "daily"
    raw = _shell(ctx, argv, timeout=120)
    try:
        import json as _json
        data = _json.loads(raw)
    except (ValueError, TypeError):
        data = {"view": view}

    elements: list = []
    cc = data.get("claude_code")
    if cc is not None:
        elements.append({"tag": "markdown",
                          "content": "**📊 Claude Code (api.anthropic.com)**"})
        if not cc.get("ok"):
            elements.append(column_set_2(
                "**Status**",
                f"<font color='red'>Claude usage 读取失败</font> · {cc.get('note', '')}"))
        else:
            metrics = cc.get("metrics") or []
            if not metrics:
                elements.append(column_set_2(
                    "**Status**",
                    f"<font color='grey'>API 跑通但没返回可解析窗口</font>"))
            for m in metrics:
                color = remaining_color(m["remaining_pct"])
                extra = m.get("extra")
                if extra:
                    right = (f"<font color='{color}'>**已用 {m['used_pct']}%**</font>"
                             f" · ${extra['used']:.2f} / ${extra['cap']} {extra['ccy']}")
                else:
                    right = (f"<font color='{color}'>**剩余 {m['remaining_pct']}%**</font>"
                             f" · 已用 {m['used_pct']}% · 重置 {m.get('reset_iso', '')}")
                elements.append(column_set_2(f"**{m['label']}**", right))
        elements.append({"tag": "hr"})

    cx = data.get("codex")
    if cx is not None:
        elements.extend(_codex_section(cx))
        elements.append({"tag": "hr"})

    km = data.get("kimi")
    if km is not None:
        elements.extend(_kimi_section(km))
        elements.append({"tag": "hr"})

    other = data.get("other_clis") or []
    if other:
        elements.append({"tag": "markdown",
                          "content": "**📦 其他 CLI**"})
        for row in other:
            elements.append(column_set_2(
                f"**{row['cli']}**",
                f"<font color='grey'>{row['note']}</font>"))
        elements.append({"tag": "hr"})

    if not (cc or cx or km or other):
        elements.append({"tag": "markdown",
                          "content": "<font color='grey'>(无数据)</font>"})

    if elements and elements[-1].get("tag") == "hr":
        elements.pop()

    elements.append({"tag": "markdown",
                      "content": (f"<font color='grey'>采集 "
                                   f"{beijing_stamp(ctx.now)} · "
                                   f"data source `claudeteam usage --json`"
                                   f"</font>")})

    cc_failed = cc and not cc.get("ok")
    cx_failed = cx and not cx.get("ok")
    km_failed = km and not km.get("ok")
    color = "red" if (cc_failed or cx_failed or km_failed) else "purple"
    return rich_card(
        f"📊 /usage ({view}) — 额度快照 {beijing_stamp(ctx.now)}",
        elements,
        color=color,
    )


def _handle_tmux(args: str, ctx: SlashContext) -> str | dict:
    """`/tmux [agent] [N]` — capture last N pane lines as a Feishu card.

    Returns a blue card with code-fenced body so the monospace pane
    content (banners / spinners / box drawing) renders aligned.
    Empty pane gets a placeholder, mirroring the CLI `claudeteam
    peek`'s `(empty buffer for X)` line.

    Unknown-agent / illegal-name still return text (warning is
    one-line — a card here would be over-formatting)."""
    parts = args.split()
    agent = parts[0] if parts else _default_agent(ctx)
    raw_lines = (int(parts[1]) if len(parts) >= 2 and parts[1].isdigit()
                 else _tmux_default_lines())
    n_lines = max(1, min(raw_lines, _tmux_max_lines()))
    _, agent_set, _, _ = _live_agents()
    if agent not in agent_set:
        return f"⚠️ 未知 agent: `{agent}`（合法名: {sorted(agent_set)}）"
    target = tmux.Target(ctx.session, agent)
    raw = tmux.capture_pane(target, lines=n_lines).rstrip() or "(窗口为空)"
    return simple_card(
        f"📺 /tmux {agent} — 最近 {n_lines} 行 [{ctx.session}]",
        fenced_block(raw),
        color="blue",
    )


def _handle_send(args: str, ctx: SlashContext) -> str:
    if not args.strip():
        return "用法: /send <agent> <message>\n例: /send worker_cc \"看一下 README\""
    parts = args.split(None, 1)
    if len(parts) < 2 or not parts[1].strip():
        return "用法: /send <agent> <message>（缺少消息内容）"
    agent, msg = parts[0].strip(), parts[1].strip()
    if (warn := _bad_agent(agent, ctx)):
        return warn
    if _config.agent_runner(agent) == "acp":
        # The pane is a read-only tail viewer — send-keys into it "succeeds"
        # at the tmux level while the agent never sees a byte. Queue it.
        from claudeteam.store import acp_queue
        if local_facts.is_retired(agent):
            return f"⏸ /send → {agent} · 已停止 (fired)，不投递"
        try:
            acp_queue.enqueue(agent, msg, sender="user")
        except OSError as e:
            return f"❌ /send → {agent} · acp 入队失败: {e}"
        return f"✅ /send → {agent}（acp 已入队）\n内容: {msg}"
    target = tmux.Target(ctx.session, agent)
    ok = tmux.inject(target, msg)
    glyph = "✅" if ok else "❌"
    return f"{glyph} /send → {ctx.session}:{agent}\n内容: {msg}"


_COMPACT_REJECT_MARKER = "can't be triggered from inside a response"


def _handle_compact(args: str, ctx: SlashContext) -> str:
    """Send /compact to agent's pane, then schedule a background
    re-identify so the agent reloads its identity.md after compaction
    settles (post-compact identity reread).

    Claude 2.x has a guard that treats programmatically-injected slash
    commands as plain text when it detects a response is in flight,
    answering "/compact is a built-in CLI command — please run it
    yourself in the terminal · It can't be triggered from inside a
    response." We can't bypass that guard from a tmux send-keys path,
    so settle a beat after inject and peek the pane: if the LLM rejected
    it, surface that to chat instead of the optimistic
    "已让 agent 自压缩上下文" line."""
    parts = args.split()
    agent = (parts[0] if parts else _default_agent(ctx)).strip()
    if (warn := _bad_agent(agent, ctx)):
        return warn
    if _config.agent_runner(agent) == "acp":
        # No REPL to type /compact into. The SDK auto-compacts on context
        # pressure; the manual escape hatch is a fresh session via /clear.
        return (f"ℹ️ {agent} 走 ACP runner，上下文由其 SDK 自动压缩；"
                f"要手动重置用 /clear {agent}（开新会话 + 重新入职）")
    # Per-CLI compaction command: claude/codex/kimi `/compact`, gemini/qwen
    # `/compress` (CliAdapter.compact_command). Inject the agent's OWN command,
    # not a hardcoded one.
    from claudeteam.agents import adapter_for_agent
    from claudeteam.runtime import wake
    try:
        adapter = adapter_for_agent(agent)
    except Exception:
        return f"⚠️ /compact → {ctx.session}:{agent} · 无法解析其 CLI 适配器"
    compact_cmd = adapter.compact_command()
    if not compact_cmd:
        return (f"⚠️ {ctx.session}:{agent}（{adapter.process_name()}）没有上下文压缩命令；"
                f"用 /clear 清空或 /restart 重启")
    target = tmux.Target(ctx.session, agent)
    ok = tmux.inject(target, compact_cmd)
    glyph = "✅" if ok else "❌"
    if not ok:
        return f"❌ {compact_cmd} → {ctx.session}:{agent} · tmux inject 失败"
    # Brief settle so the CLI either starts compacting (REPL slash route) or
    # hands the message to the LLM (text route). 2s is enough to surface the
    # rejection marker without blocking the chat reply long enough to time out.
    ctx.sleep(2.0)
    pane = tmux.capture_pane(target, lines=20) or ""
    if _COMPACT_REJECT_MARKER in pane:
        return (f"⚠️ {compact_cmd} → {ctx.session}:{agent} · CLI 把命令当成消息文本回了"
                f"（claude 2.x autoinject 时常见）。建议 /clear 替代。")
    # Schedule the re-identify on a background thread so the bot reply comes
    # back immediately. Verified inject — the big identity prompt can be
    # dropped on a codex/qwen/kimi composer if fired-and-forgotten.
    init_msg = identity.init_prompt(agent)

    def _reidentify_later():
        ctx.sleep(_REIDENTIFY_DELAY_S)
        wake.inject_and_confirm(target, adapter, init_msg)

    ctx.background(_reidentify_later)
    return (f"{glyph} {compact_cmd} → {ctx.session}:{agent} · 已让 agent 自压缩上下文 · "
            f"{int(_REIDENTIFY_DELAY_S)}s 后自动重注 identity")


def _stop_one(agent: str, ctx: SlashContext) -> tuple[bool, str]:
    """Interrupt `agent`'s current action. ACP agents get a durable
    `cancel` control row (the AcpHost fires session/cancel — deterministic,
    unlike a hopeful Esc). Tmux agents get the CLI-specific interrupt key
    (default Esc; see CliAdapter.interrupt_keys). Adapter lookup is
    defensive — an unknown cli falls back to Esc rather than skipping.

    Returns (ok, method) so the chat receipt can say what ACTUALLY
    happened — telling the operator "已送中断键（Esc）" for an ACP cancel
    misleads any later audit (acceptance F-6)."""
    if _config.agent_runner(agent) == "acp":
        from claudeteam.runtime import acp_host
        return acp_host.request_cancel(agent), "acp session/cancel"
    from claudeteam.agents import adapter_for_agent
    try:
        keys = adapter_for_agent(agent).interrupt_keys()
    except Exception:
        keys = ["Escape"]
    return tmux.send_keys(tmux.Target(ctx.session, agent), *keys), f"中断键 {keys[0]}"


def _handle_stop(args: str, ctx: SlashContext) -> str:
    """`/stop [agent]` — interrupt an agent's current action by sending its
    CLI's interrupt key (default Esc — see CliAdapter.interrupt_keys, which
    unifies the behavior across claude-code / codex / gemini / qwen / kimi).

    No argument → stop EVERY agent in the team: an emergency "everyone halt
    now" that fans the interrupt out to each pane instead of erroring on a
    missing target."""
    arg = args.strip()
    if not arg:
        names, _, _, _ = _live_agents()
        if not names:
            return "⚠️ 团队里没有 agent 可停止"
        results = [(n, *_stop_one(n, ctx)) for n in names]
        ok_n = sum(1 for _, ok, _ in results if ok)
        lines = "\n".join(
            f"{'✅' if ok else '❌'} {ctx.session}:{n}（{method}）"
            for n, ok, method in results)
        return (f"🛑 /stop 全员 · 已向 {len(results)} 个 agent 发中断，"
                f"{ok_n}/{len(results)} 成功\n{lines}")
    agent = arg.split()[0]
    if (warn := _bad_agent(agent, ctx)):
        return warn
    ok, method = _stop_one(agent, ctx)
    glyph = "✅" if ok else "❌"
    return f"{glyph} /stop → {ctx.session}:{agent} · 已发中断（{method}）"


def _handle_clear(args: str, ctx: SlashContext) -> str:
    if not args.strip():
        return ("用法: /clear <agent>\n"
                "例: /clear worker_cc（清上下文 + 重新入职 init_msg，相当于 rehire）\n"
                "⚠️ 会丢 agent 当前会话记忆，谨慎用")
    agent = args.strip().split()[0]
    if (warn := _bad_agent(agent, ctx)):
        return warn
    if _config.agent_runner(agent) == "acp":
        # ACP context reset = discard the saved session (next message opens
        # a fresh one and re-runs the identity turn) + stop the subprocess.
        from claudeteam.runtime import acp_host
        if not acp_host.recycle(agent):
            return f"❌ /clear → {agent} · acp 会话重置失败"
        return (f"✅ /clear → {agent} · acp 会话已作废，"
                f"下一条消息将开新会话并重新入职")
    # All five CLIs expose `/clear`, but get it from the adapter rather than
    # hardcoding — so a CLI that names it differently stays correct.
    from claudeteam.agents import adapter_for_agent
    from claudeteam.runtime import wake
    try:
        adapter = adapter_for_agent(agent)
    except Exception:
        return f"⚠️ /clear → {ctx.session}:{agent} · 无法解析其 CLI 适配器"
    clear_cmd = adapter.clear_command()
    target = tmux.Target(ctx.session, agent)
    if clear_cmd:
        if not tmux.inject(target, clear_cmd):
            return f"❌ {clear_cmd} → {ctx.session}:{agent} · 送清屏命令失败"
        ctx.sleep(2.0)
    # The agent just lost its context — re-inject identity. Verified submit:
    # a big multi-line prompt can be dropped on a codex/qwen/kimi composer.
    if not wake.inject_and_confirm(target, adapter, identity.init_prompt(agent)):
        return (f"⚠️ /clear → {ctx.session}:{agent} · {clear_cmd or ''} 已送但 "
                f"identity 重注入未确认")
    return (f"✅ /clear → {ctx.session}:{agent} · 已 {clear_cmd or '(无清屏命令)'}"
            f"（{adapter.process_name()}）+ 重新入职")


# Kanban columns rendered in this fixed order; same status vocabulary as
# store.tasks.VALID_STATUSES. Glyphs let the boss scan a column at a glance.
_TASK_COLUMNS = (
    ("待处理", "📋"),
    ("进行中", "🔄"),
    ("需审批", "⏳"),
    ("已完成", "✅"),
    ("已取消", "🚫"),
)


def _handle_task(args: str, ctx: SlashContext) -> dict:
    """/task [all] → read-only kanban of the authoritative task store.

    Reads tasks.json, groups every task by status in `_TASK_COLUMNS`
    order, and lists `id · title · 👤assignee` per row — with the verbatim
    intent back-link (`↳ I-n: …`) when the task carries one, so the boss
    can trace a task to the original ask. Empty columns are marked 无.

    Terminal columns (已完成/已取消) render COLLAPSED by default — header
    with count only, no item rows — so a long-lived store doesn't bury
    the live work under dozens of finished entries. `/task all` (or
    `/task 全部`) expands them. Active columns are never folded: 待处理 /
    进行中 / 需审批 are exactly what the boss opens the kanban for.

    Pure render: never creates/updates/transitions a task. Card turns
    yellow when anything sits in 需审批 (awaiting the boss), green else
    — folding never affects the color signal (需审批 is never folded).
    """
    expand = args.strip().lower() in ("all", "全部")
    rows = tasks.list_tasks()
    buckets: dict[str, list[dict]] = {name: [] for name, _ in _TASK_COLUMNS}
    for t in rows:
        buckets.setdefault(t.get("status", ""), []).append(t)

    folded = False
    body_lines: list[str] = []
    for name, glyph in _TASK_COLUMNS:
        group = buckets.get(name, [])
        fold = (not expand and group
                and name in tasks.TERMINAL_STATUSES)
        if fold:
            body_lines.append(f"{glyph} **{name}**（{len(group)}）▸ 已折叠")
            folded = True
            continue
        body_lines.append(f"{glyph} **{name}**（{len(group)}）")
        if not group:
            body_lines.append("　无")
            continue
        for t in group:
            line = f"　`{t['id']}` {t.get('title') or '(无标题)'}"
            assignee = t.get("assignee")
            if assignee:
                line += f" · 👤{assignee}"
            body_lines.append(line)
            iid = t.get("intent_id")
            if iid:
                intent = tasks.get_intent(iid)
                raw = (intent or {}).get("raw_text", "").replace("\n", " ")
                snippet = raw[:24] + ("…" if len(raw) > 24 else "")
                tail = f": {snippet}" if snippet else ""
                body_lines.append(f"　　↳ {iid}{tail}")
    if folded:
        body_lines.append("")
        body_lines.append("_（终态条目已折叠为计数，`/task all` 展开明细）_")

    if not rows:
        body_lines = ["_(暂无任何 task)_"]

    color = "yellow" if buckets.get("需审批") else "green"
    return simple_card(
        f"🗂️ /task — 任务看板 {beijing_stamp(ctx.now)}",
        "\n".join(body_lines),
        color=color,
    )


# ── team control: /shutdown /restart /login ───────────────────────
#
# Safety model (defense in depth, three layers):
#   L1  classify_event drops any event whose chat_id != this team's
#       ("cross_team", router.py) BEFORE it can become a slash — a
#       /shutdown from the live group never reaches the test router.
#   L2  down/up only touch config.session_name() + local pidfiles, so
#       they are container-local — can't reach another deployment.
#   L3  these handlers are DEFAULT-DENY: gated behind explicit per-feature
#       opt-ins (controls.allow_lifecycle_slash / controls.allow_login_slash)
#       that the live maintenance team never sets. Even a misconfigured
#       chat_id can't tear down or re-auth a team that didn't opt in.


def _lifecycle_gate() -> dict | None:
    """Refusal card if chat-driven team teardown/restart isn't enabled for
    this deployment; None if allowed. The live maintenance team never sets
    the flag, so /shutdown & /restart are inert there by construction."""
    from claudeteam.runtime import teamctl
    if not teamctl.lifecycle_slash_enabled():
        return simple_card(
            "🔒 团队生命周期控制未开启",
            "本部署未开启聊天端 down/up 控制。\n"
            "如确需，在 claudeteam.toml 设 `[controls] allow_lifecycle_slash = true` "
            "后重启 router。\n"
            "（live 维护群默认关闭 = 从作用域封死，无法被误操作下线。）",
            color="red")
    return None


_SHUTDOWN_CONFIRM = {"确认", "confirm", "yes", "y"}


def _handle_shutdown(args: str, ctx: SlashContext) -> dict:
    """`/shutdown` — take the team offline by killing the agent tmux panes,
    but KEEP the router + its feishu subscription + the watchdog alive so a
    later `/restart` can be received and re-wake the team (no shell access
    needed). Two-step: a bare `/shutdown` previews; `/shutdown 确认` does it
    via a DETACHED runner that posts a '已下线' card when finished."""
    if (gate := _lifecycle_gate()) is not None:
        return gate
    from claudeteam.runtime import teamctl
    if args.strip().lower() not in _SHUTDOWN_CONFIRM:
        names, _, _, _ = _live_agents()
        return simple_card(
            "⚠️ 确认下线团队？",
            f"`/shutdown` 将下线本团队（session `{ctx.session}`）：\n"
            f"• 杀掉 tmux 会话与全部 {len(names)} 个 agent pane\n"
            f"• **保留 router + 飞书订阅 + watchdog 在线**（这样 `/restart` 仍能被收到）\n\n"
            f"下线后直接 `/restart` 即可重新唤醒团队，无需运维登服务器。\n"
            f"**确认请回复 `/shutdown 确认`。**",
            color="yellow")
    teamctl.spawn_detached(["team-shutdown"])
    return simple_card(
        "🛑 团队下线中…",
        f"已触发 agent 下线（detached, session `{ctx.session}`）；router/订阅保留在线。"
        "完成后补一张『已下线』卡。",
        color="yellow")


def _handle_restart(args: str, ctx: SlashContext) -> dict:
    """`/restart` — restart the whole team (≈ down then up). Direct (no
    confirm — it's recoverable, the team comes back). Runs in a DETACHED
    runner: robust down (which also clears stragglers — stale pidfiles,
    orphan tmux, leftover npx/node groups), abort if anything refuses to
    die, else up; posts a '已重启' card when finished."""
    if (gate := _lifecycle_gate()) is not None:
        return gate
    from claudeteam.runtime import teamctl
    teamctl.spawn_detached(["team-restart"])
    return simple_card(
        "♻️ 团队重启中…",
        f"已触发 down→up（detached, session `{ctx.session}`）。"
        "先清残留（死 pidfile / 孤儿 tmux / 旧 router 进程组）再拉起；"
        "若有东西杀不掉会中止 up 并报错。完成后补一张『已重启』卡。",
        color="yellow")


# /login ────────────────────────────────────────────────────────────
#
# cli alias → canonical adapter name.
_LOGIN_CLI_ALIASES = {
    "cc": "claude-code", "claude": "claude-code", "claude-code": "claude-code",
    "codex": "codex-cli", "codex-cli": "codex-cli",
    "kimi": "kimi-code", "kimi-code": "kimi-code",
    "gemini": "gemini-cli", "gemini-cli": "gemini-cli",
    "qwen": "qwen-code", "qwen-code": "qwen-code",
}

# Per-CLI re-auth spec for the ZERO-LLM path: the router runs `login_argv`
# as a DIRECT subprocess (never injected into the agent pane), so /login
# works even when the agent's model is down / token is expired (401) — the
# exact recovery scenario. `env_var` is the per-agent credential-home env
# the subprocess needs (so the token writes to the isolated /data home, not
# the host); `cred_relpath` is under that home. `shared_home` flags CLIs
# whose creds are NOT per-agent-isolated (kimi) — those are hard-refused by
# the allowlist anyway. Zero-LLM: codex login --device-auth prints URL+code
# to stdout (e.g. "one-time code: MMLU-W452Y"); claude auth login is a real
# subcommand. `env_key` is the secrets-.env var agent_auth would use instead
# — the right path for the CLIs that can't be scripted (gemini/qwen).
#
# Verified mid-2026 against the installed CLIs + current source:
#   claude `auth login`         — PASTE-BACK (prints URL, waits on stdin for
#                                  the pasted code) → interactive, Path B.
#   codex `login --device-auth` — DEVICE-CODE, self-polling, fire-and-forget
#                                  (URL + code to stdout) → Path A, pure chat.
#   kimi `login --json`         — device-code self-polling too, but shared
#                                  ~/.kimi → allowlist hard-refuses it.
#   gemini / qwen               — no re-auth subcommand at all → login_argv
#                                  None → /login surfaces the .env-key card.
# `interactive` True = paste-back (Path B: router can't relay the stdin paste
# across the ~135s respawn the URL→authorize→paste window spans, so it guides
# the operator to finish in the pane — still zero-LLM, works at 401). False =
# device-code fire-and-forget (Path A, full pure-chat). claude is the only
# interactive one.
_LOGIN_SPEC = {
    "claude-code": {"login_argv": ["claude", "auth", "login"], "interactive": True,
                    "env_var": "HOME",       "cred_relpath": ".claude/.credentials.json",
                    "shared_home": False, "env_key": "CLAUDE_CODE_OAUTH_TOKEN"},
    "codex-cli":   {"login_argv": ["codex", "login", "--device-auth"], "interactive": False,
                    "env_var": "CODEX_HOME", "cred_relpath": ".codex/auth.json",
                    "shared_home": False, "env_key": "CODEX_ACCESS_TOKEN"},
    # gemini/qwen have NO standalone re-auth subcommand (gemini never did;
    # qwen's `auth` was removed 2026-06-22 + its free OAuth tier is gone) →
    # login_argv=None routes /login to the "set an API key in .env" card.
    "gemini-cli":  {"login_argv": None, "interactive": False,
                    "env_var": "HOME",       "cred_relpath": ".gemini/oauth_creds.json",
                    "shared_home": False, "env_key": "GEMINI_API_KEY"},
    "qwen-code":   {"login_argv": None, "interactive": False,
                    "env_var": "HOME",       "cred_relpath": ".qwen/oauth_creds.json",
                    "shared_home": False, "env_key": "DASHSCOPE_API_KEY"},
    # kimi login is device-code self-polling (NOT interactive paste-back) and
    # --json gives clean events — but kimi shares ~/.kimi (shared_home) so the
    # allowlist hard-refuses it regardless.
    "kimi-code":   {"login_argv": ["kimi", "login", "--json"], "interactive": False,
                    "env_var": "HOME",       "cred_relpath": ".kimi",
                    "shared_home": True, "env_key": None},
}


def _login_env(cli: str, agent: str) -> dict:
    """Build the subprocess env so the CLI's login writes its token to the
    agent's ISOLATED per-agent home (/data/agent-home/<agent>...), never the
    host's. claude/gemini/qwen key off HOME; codex off CODEX_HOME
    (= <agent_home>/.codex). Mirrors the spawn env in agents/*.py."""
    import os
    from claudeteam.runtime.paths import agent_home
    env = dict(os.environ)
    spec = _LOGIN_SPEC[cli]
    if spec["env_var"] == "CODEX_HOME":
        codex_home = f"{agent_home(agent)}/.codex"
        # codex hard-errors if CODEX_HOME is set but the dir is missing.
        os.makedirs(codex_home, exist_ok=True)
        env["CODEX_HOME"] = codex_home
    else:
        env["HOME"] = agent_home(agent)
    return env

# Whitelist scrapers for the verification SURFACE only. We surface a login
# URL + a short human-facing pairing/device code — both are meant to be
# shown to a person and are not secrets. We NEVER echo the raw pane (which
# can contain the resulting access token after the flow completes) and
# NEVER a long token-shaped string.
_LOGIN_URL_RE = re.compile(
    r"https?://[^\s'\"<>]+", re.IGNORECASE)
# Pairing codes: short, human-typeable. Two shapes, each its own regex so
# `findall` returns the full match cleanly (a single combined pattern with
# one capture group silently returns '' for the un-grouped alternative).
#   - XXXX-XXXXX style device codes (group lengths vary: codex emits 4-5
#     e.g. MLGX-BJYY9, GitHub 4-4; allow 4-6 each side with word boundaries)
#   - a value labeled "code:" / "码:" — hyphen allowed, length-capped so a
#     long opaque token can never be mistaken for a pairing code.
# A captured code must contain at least one LETTER: real device codes are
# alphanumeric, whereas a pure-digit dddd-dddd is almost always a date /
# anchor — a persistent [QUICKSTART-0613-1913]-style anchor in a worker pane
# would otherwise be mis-surfaced as the codex device code.
_LOGIN_CODE_PAIR_RE = re.compile(r"\b[A-Z0-9]{4,6}-[A-Z0-9]{4,6}\b")
_LOGIN_CODE_LABELED_RE = re.compile(
    r"(?:code|码)[:：]\s*([A-Za-z0-9][A-Za-z0-9-]{3,11})\b", re.IGNORECASE)
# Defense: never surface a captured line that smells like a secret/token.
_SECRET_HINT_RE = re.compile(
    r"sk-|ya29\.|eyJ|access[_-]?token|refresh[_-]?token|api[_-]?key|"
    r"bearer\s|secret|-----BEGIN", re.IGNORECASE)
# CLI login output is colorized — codex wraps the device code in ANSI
# (\x1b[94mMNOG-E7MBX\x1b[0m), which makes the bare-code regex miss it and
# leaks [0m residue into the URL. Strip ANSI/CSI escape
# sequences before scraping so the URL + code come out clean.
_ANSI_RE = re.compile(r"\x1b(?:[@-Z\\-_]|\[[0-?]*[ -/]*[@-~])")


def _extract_login_surface(pane_text: str) -> dict:
    """Pull ONLY the verification URL + short pairing code out of captured
    text. Security-critical: returns nothing for any line that looks like it
    carries a secret/token, and caps code length so an access token can never
    be mistaken for a pairing code. ANSI escapes are stripped first (login
    output is colorized). Pure + table-tested."""
    urls: list[str] = []
    codes: list[str] = []
    for line in _ANSI_RE.sub("", pane_text).splitlines():
        if _SECRET_HINT_RE.search(line):
            continue  # never surface a line that might carry a token
        for m in _LOGIN_URL_RE.findall(line):
            # only verification-style URLs, and never one with a token query
            if _SECRET_HINT_RE.search(m):
                continue
            if any(k in m.lower() for k in
                   ("login", "oauth", "auth", "verify", "device", "activate", "callback")):
                urls.append(m)
        for code in (_LOGIN_CODE_PAIR_RE.findall(line)
                     + _LOGIN_CODE_LABELED_RE.findall(line)):
            code = code.strip()
            if (code and len(code) <= 12 and any(c.isalpha() for c in code)
                    and not _SECRET_HINT_RE.search(code)):
                codes.append(code)
    # de-dup, preserve order
    return {"urls": list(dict.fromkeys(urls)), "codes": list(dict.fromkeys(codes))}


def _login_gate() -> dict | None:
    """Refusal card if chat-driven CLI re-auth isn't enabled. Separate from
    the lifecycle gate so /shutdown & /restart can be demoed while /login
    stays inert until creds are isolated from the host (container panes run
    HOME=/root with host-mounted cred files — a /login write there would
    clobber the operator's personal credentials)."""
    from claudeteam.runtime import teamctl
    if not teamctl.login_slash_enabled():
        return simple_card(
            "🔒 /login 未开启",
            "聊天端 CLI 重新认证未开启。\n"
            "需在 claudeteam.toml 设 `[controls] allow_login_slash = true` 后重启 router；"
            "且仅当容器凭证已与宿主机隔离（HOME=容器本地 agent-home）时才可开启，"
            "否则 /login 会覆盖宿主机本人凭证。",
            color="red")
    return None


def _login_resolve_agent(cli: str, agent_arg: str, ctx: SlashContext) -> tuple[str, dict | None]:
    """Resolve which agent's CLI to re-auth. Returns (agent, error_card).
    Honors an explicit agent arg; else picks the sole agent on that cli;
    else asks the operator to disambiguate."""
    names, agent_set, _, agents_dict = _live_agents()
    if agent_arg:
        if agent_arg not in agent_set:
            return "", simple_card("⚠️ 未知 agent",
                                   f"`{agent_arg}` 不在花名册（合法名: {sorted(agent_set)}）",
                                   color="red")
        return agent_arg, None
    matches = [n for n in names if agents_dict.get(n, {}).get("cli") == cli]
    if not matches:
        return "", simple_card("⚠️ 没有该 CLI 的 agent",
                               f"花名册里没有跑 `{cli}` 的 agent。", color="red")
    if len(matches) > 1:
        return "", simple_card("⚠️ 多个 agent 跑该 CLI",
                               f"`{cli}` 有多个 agent：{matches}。"
                               f"请指定：`/login {cli} <agent>`", color="yellow")
    return matches[0], None


def _handle_login(args: str, ctx: SlashContext) -> dict:
    """`/login <cli> [agent]` — trigger a CLI's re-auth flow and surface the
    verification URL + pairing code to the operator. The CLI writes the new
    token back to its own cred file; it takes effect on the agent's next
    call — we do NOT auto-restart.

    Two safety layers: the master `allow_login_slash` gate, then a per-CLI
    isolation allowlist (`login_allowed_clis`). A CLI whose creds are NOT
    isolated from the host — kimi shares ~/.kimi, and any deployment whose
    panes run a host-mounted cred dir — is HARD-REFUSED, not merely warned:
    a /login write there would clobber the operator's personal credentials."""
    if (gate := _login_gate()) is not None:
        return gate
    parts = args.split()
    if not parts:
        return simple_card("用法",
                           "`/login <cli> [agent]`\n例：`/login cc` / `/login codex worker_x`\n"
                           f"支持的 cli：{sorted(set(_LOGIN_CLI_ALIASES))}", color="blue")
    alias = parts[0].strip().lower()
    cli = _LOGIN_CLI_ALIASES.get(alias)
    if cli is None:
        return simple_card("⚠️ 未知 CLI",
                           f"`{alias}` 不认识。支持：{sorted(set(_LOGIN_CLI_ALIASES))}",
                           color="red")
    # Per-CLI isolation gate: only re-auth CLIs whose creds are isolated from
    # the host in THIS deployment, else a /login write clobbers the operator's
    # personal credentials. kimi (shared ~/.kimi) and any un-isolated CLI are
    # HARD-REFUSED until explicitly allowlisted post-isolation.
    from claudeteam.runtime import teamctl
    allowed = teamctl.login_allowed_clis()
    if cli not in allowed:
        return simple_card(
            "🚫 该 CLI 的 /login 未启用",
            f"`{cli}` 的凭证在本部署**未与宿主机隔离**"
            f"（如 kimi 共享 ~/.kimi），为避免覆盖老板本机凭证，/login 对它**硬拒**。\n"
            f"隔离完成后在 claudeteam.toml `[controls] login_allowed_clis` 加入它。\n"
            f"当前允许：{sorted(allowed)}",
            color="red")
    agent_arg = parts[1].strip() if len(parts) > 1 else ""
    agent, err = _login_resolve_agent(cli, agent_arg, ctx)
    if err is not None:
        return err

    spec = _LOGIN_SPEC[cli]
    warn_line = ""
    if spec["shared_home"]:
        warn_line = ("\n\n⚠️ **共享 HOME**：本次登录会影响**所有**该 CLI pane 的凭证。")

    if spec["login_argv"] is None:
        # No standalone re-auth subcommand for this CLI (gemini never had one;
        # qwen's was removed) — point the operator at the .env credential route,
        # which is the right path for these anyway.
        ek = spec["env_key"] or "API key"
        return simple_card(
            f"🔑 /login {alias} → {agent} · 该 CLI 无可脚本化登录",
            f"`{cli}` 没有可被 router 直接拉起的重登子命令"
            f"（gemini 从来没有；qwen 的已于 2026-06-22 移除）。\n"
            f"**正确做法**：在 secrets `.env`（默认 `<state>/.env`）配 `{ek}=<key>`"
            f"（单独给本 agent 用 `<AGENT大写>_{ek}`）——`agent_auth` 会按 "
            f"token>login>api_key 自动采用，下次该 agent 起 CLI 时生效。",
            color="yellow")

    argv_str = " ".join(spec["login_argv"])
    if spec["interactive"]:
        # Path B — interactive (stdin-paste) login. A pure-chat relay can't
        # robustly survive the router respawn the authorize→paste window
        # spans, so guide the operator to complete it in the agent's pane.
        # Still ZERO-LLM: `claude auth login` is a CLI auth command (not
        # model inference), so it works even at 401 / model-down.
        return simple_card(
            f"🔑 /login {alias} → {agent} · 需在 pane 内完成",
            f"`{cli}` 的登录是**交互式**（给一个授权 URL，再要你把授权后拿到的码"
            f"粘回）。这步跨容器 router 重起会断，聊天通道无法可靠闭环，所以请在 "
            f"**{agent} 的 pane 里直接跑** `{argv_str}`：打开它给的 URL 授权、粘码完成。\n"
            f"这步是 **CLI 认证、不经大模型**（token 过期 / 模型掉线也能登），"
            f"token 写回该 agent 隔离 cred home。\n"
            f"（codex 这类非交互 CLI 走纯机械 /login，聊天里直接出码。）"
            + warn_line,
            color="yellow")
    # Path A — fire-and-forget (ZERO-LLM): the router runs the CLI's
    # login as a DIRECT subprocess (never injected into the agent pane), so it
    # works when the agent's model is down / token expired (401). Streams
    # stdout to a temp file, polls for the URL+code, posts a follow-up card.
    # Token writes to the agent's ISOLATED cred home, not the host's.
    env = _login_env(cli, agent)
    ctx.background(lambda: _login_run_subprocess(ctx, spec["login_argv"], env,
                                                 alias, agent, cli))
    return simple_card(
        f"🔑 /login {alias} → {agent} · 已触发（纯机械）",
        f"router 已直接拉起 `{argv_str}`（**不经 agent 大模型**，"
        f"模型掉线 / token 过期也能登），正在等验证链接 / 设备码……\n"
        f"出码后**自动补一张卡**。登录完成后 token 写回该 CLI 凭证文件，"
        f"**下次调用自然生效**（无需重启）。"
        + warn_line,
        color="blue")


# Background poll cadence for the login-subprocess stdout. ctx.sleep is real
# time in production; tests inject a short/no-op sleep so the loop is quick.
_LOGIN_POLL_INTERVAL_S = 5.0
_LOGIN_POLL_ATTEMPTS = 18          # ~90s total


def _login_surface_body(surface: dict) -> str:
    body = []
    if surface["urls"]:
        body.append("**验证链接**（点开在浏览器完成登录）：")
        body.extend(surface["urls"])
    if surface["codes"]:
        body.append("\n**设备码**：" + " / ".join(f"`{c}`" for c in surface["codes"]))
    body.append("\n登录完成后新 token 写回该 CLI 凭证文件，**下次调用自然生效**（无需重启）。")
    return "\n".join(body)


def _login_post_card(title: str, body: str, *, color: str) -> None:
    """Post a follow-up /login card to the team chat from the background
    runner (the original slash reply already returned). Best-effort: never
    raise."""
    try:
        from claudeteam.feishu import chat
        from claudeteam.runtime import config
        cid = config.chat_id()
        if cid:
            chat.send_card(cid, simple_card(title, body, color=color),
                           profile=config.lark_profile())
    except Exception:
        pass


def _login_run_subprocess(ctx: SlashContext, argv: list, env: dict,
                          alias: str, agent: str, cli: str) -> None:
    """ZERO-LLM login: run the CLI's login subprocess DIRECTLY (no agent pane,
    no agent LLM), stream its stdout to a temp file, poll the file for the
    verification URL+code, post a follow-up card. Works when the agent's model
    is down / token is expired — the recovery scenario this exists for.

    The subprocess is detached (start_new_session) so it survives this poll
    thread / a router restart to complete the device auth + write the token.
    We intentionally never kill it. Best-effort: any failure is swallowed."""
    import os
    import subprocess
    import tempfile
    try:
        fd, out_path = tempfile.mkstemp(prefix="ct-login-", suffix=".out")
        os.close(fd)
        try:
            out = open(out_path, "wb")
            from claudeteam.util import detached_popen_kwargs
            proc = subprocess.Popen(argv, env=env, stdout=out,
                                    stderr=subprocess.STDOUT,
                                    stdin=subprocess.DEVNULL,
                                    **detached_popen_kwargs())
            out.close()   # child keeps its dup'd fd; parent handle not needed
        except (OSError, ValueError) as e:
            _login_post_card(
                f"❌ /login {alias} → {agent}",
                f"router 拉起 `{argv[0]}` 失败：{e}。确认该 CLI 在容器内可执行。",
                color="red")
            return
        for _ in range(_LOGIN_POLL_ATTEMPTS):
            ctx.sleep(_LOGIN_POLL_INTERVAL_S)
            try:
                txt = open(out_path, encoding="utf-8", errors="replace").read()
            except OSError:
                txt = ""
            surface = _extract_login_surface(txt)
            if surface["urls"] or surface["codes"]:
                _login_post_card(f"🔑 /login {alias} → {agent} · 验证信息",
                                 _login_surface_body(surface), color="blue")
                return
            if proc.poll() is not None:
                # exited without ever printing a surface → already logged in,
                # or errored. Surface the tail so the operator can tell which.
                tail = (txt.strip().splitlines() or ["(无输出)"])[-1][:200]
                _login_post_card(
                    f"🔑 /login {alias} → {agent} · 无验证码",
                    f"`{argv[0]}` 登录进程已退出但没出验证链接 / 码——可能已登录或出错。"
                    f"末行：`{tail}`。可重试或 `/tmux {agent}` 看。", color="yellow")
                return
        _login_post_card(
            f"🔑 /login {alias} → {agent} · 暂未捕获",
            f"等了 ~{int(_LOGIN_POLL_INTERVAL_S * _LOGIN_POLL_ATTEMPTS)}s 仍没抓到验证链接 / "
            f"设备码。可重试或 `/tmux {agent}` 看。", color="yellow")
    except Exception:
        pass


def _handle_standup(args: str, ctx: SlashContext) -> str:
    """`/standup` — trigger one 巡视汇报 immediately (and reset the
    ticker's cadence clock). The periodic firing is configured in
    claudeteam.toml `[standup]` and runs inside the router."""
    from claudeteam.runtime import standup
    if standup.trigger_now(log=lambda *a, **k: None):
        return ("📣 /standup · 已让 manager 巡视全员并汇报进度"
                "（定时巡视间隔见 claudeteam.toml [standup]）")
    return "❌ /standup · 巡视请求发送失败（manager 不在 roster 或投递失败）"


_HANDLERS: dict[str, Callable[[str, SlashContext], str]] = {
    "/help": _handle_help,
    "/team": _handle_team,
    "/health": _handle_health,
    "/usage": _handle_usage,
    "/tmux": _handle_tmux,
    "/send": _handle_send,
    "/compact": _handle_compact,
    "/stop": _handle_stop,
    "/clear": _handle_clear,
    "/task": _handle_task,
    "/shutdown": _handle_shutdown,
    "/restart": _handle_restart,
    "/login": _handle_login,
    "/standup": _handle_standup,
}
# 14 chat slash commands: /help /team /health /usage /tmux /send
# /compact /stop /clear /task /shutdown /restart /login /standup.
# Memory commands (`claudeteam recall` / `forget` / `remember`) live only
# as agent-pane CLIs, not chat slash.


def _split_cmd(text: str) -> tuple[str, str]:
    """Split '/cmd rest…' into ('/cmd', 'rest…'). Whitespace-tolerant."""
    parts = text.strip().split(None, 1)
    if not parts:
        return "", ""
    return parts[0], parts[1] if len(parts) > 1 else ""


def dispatch(text: str, ctx: SlashContext) -> str | dict:
    """Route `text` to its handler, return the reply.

    Return type is union: legacy handlers return `str` (sent as plain text);
    cards-aware handlers return `dict` (Feishu card schema, sent via
    `chat.send_card`). `deliver._apply_slash` branches on type.

    Unknown commands get a `/help` suggestion. Handler exceptions are
    caught and returned as `⚠️ slash handler error: …` so a buggy
    handler can't take the router daemon down.
    """
    if not text:
        return "⚠️ empty slash command"
    cmd, args = _split_cmd(text)
    handler = _HANDLERS.get(cmd)
    if handler is None:
        return f"⚠️ 未知斜杠命令: `{text.strip()}` — 试 /help 看支持的命令清单"
    try:
        return handler(args, ctx)
    except Exception as e:
        return f"⚠️ slash handler error: {e}"
