"""Slash-command dispatch — zero-LLM router-level handlers.

When the chat receives a message starting with `/`, `feishu/router.py`
emits `Decision(Action.SLASH, text=raw_text)`. `feishu/deliver.py` calls
`dispatch(text, ctx)` here, gets a `str | dict` reply, and posts it
back to the chat — `str` via `chat.send_text`, `dict` (Feishu card
schema) via `chat.send_card`. **No worker pane is touched, no LLM runs.**

Supported commands (matches main's 9-command surface):

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
    /stop <agent>                      send Ctrl-C to agent pane
    /clear <agent>                     /clear + re-init (rehire shape)

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
from claudeteam.feishu import pane_state
from claudeteam.feishu.cards import (
    beijing_stamp, column_set_2, column_set_3, fenced_block, load_color,
    remaining_color, rich_card, simple_card,
)
from claudeteam.runtime import tmux
from claudeteam.store import local_facts
from claudeteam.util import fmt_bytes


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
    from claudeteam.runtime import config as _config
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
/usage                   → claude-code 用量（ccusage 包装，卡片）
/health                  → 主机 + 员工资源占用（卡片）
/tmux [agent] [lines]    → capture-pane 窗口（默认 manager/10 行）
/send <agent> <msg>      → 直接注入消息到 agent 窗口
/compact [agent]         → 群聊无参=压缩 manager；带参压缩指定 agent
/stop <agent>            → 送 C-c 到 agent 窗口（中断当前动作）
/clear <agent>           → 送 /clear + 重新入职 init_msg（相当于 rehire）"""


def _handle_help(args: str, ctx: SlashContext) -> dict:
    """/help → interactive card. Returning a dict (instead of str) signals
    deliver._apply_slash to send via chat.send_card. The body is the same
    text block used historically; only the wrapper changes."""
    return simple_card("🆘 ClaudeTeam 自定义斜杠命令", _HELP_TEXT)


def _handle_team(args: str, ctx: SlashContext) -> dict:
    """Capture each agent's pane, classify state, return as Feishu card.

    Round-80: was a plain-text table; now a card with header containing
    the timestamp + session, body listing one `**emoji agent**: brief`
    line per agent followed by a tally summary.

    Color is `green` when no agent is in a warning/down state, `yellow`
    when at least one is, so the boss can scan group chat at a glance.

    Lazy agents (config `lazy: true`) display as ⏸ instead of 🛑 —
    a not-yet-spawned CLI is intentional, not a failure, and
    shouldn't paint the team header yellow.
    """
    # _live_agents reads config every call so claudeteam.toml edits
    # take effect on the next /team without a router restart.
    team_agents, _, lazy_agents, _ = _live_agents()

    rows = []
    tally: Counter[str] = Counter()
    for agent in team_agents:
        # Fired agents (status="已停止" via `claudeteam fire`) have no pane;
        # without this branch /team showed them as "🛑 CLI down" — same
        # as a crashed agent — and operators couldn't tell intentional
        # firing from a real failure. Caught 2026-05-09.
        status = local_facts.get_status(agent)
        if status and status.get("status") == "已停止":
            note = (status.get("task") or status.get("note")
                    or status.get("blocker") or "已停止")
            rows.append((agent, "🚫", f"已停止 ({note})"))
            tally["🚫"] += 1
            continue
        target = tmux.Target(ctx.session, agent)
        try:
            buf = tmux.capture_pane(target, lines=80)
        except Exception:
            buf = ""
        emoji, brief = pane_state.parse(buf)
        # pane_state.parse returns 🛑 (Linux bash prompt) or 🔘 (tail
        # fallback / macOS % prompt) for "no CLI" — both are normal
        # for a lazy agent before its first message arrives. Flip to
        # ⏸ so the team-color check below stays green.
        if emoji in ("🛑", "🔘") and agent in lazy_agents:
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
    # Yellow when alarms exist, otherwise purple (matches main's branding).
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
    target = tmux.Target(ctx.session, agent)
    ok = tmux.inject(target, "/compact")
    glyph = "✅" if ok else "❌"
    if not ok:
        return f"❌ /compact → {ctx.session}:{agent} · tmux inject 失败"
    # Brief settle so claude has a chance to either start compacting
    # (REPL slash route) or hand the message to the LLM (text route).
    # 2s is enough to surface the rejection marker without blocking the
    # chat reply long enough to time out.
    ctx.sleep(2.0)
    pane = tmux.capture_pane(target, lines=20) or ""
    if _COMPACT_REJECT_MARKER in pane:
        return (f"⚠️ /compact → {ctx.session}:{agent} · claude 把 /compact 当成"
                f"消息文本回了（autoinjected 时常见，2.x 行为）。建议 /clear 替代。")
    # Schedule the re-identify on a background thread so the bot reply
    # comes back to chat immediately (no 45s block).
    init_msg = identity.init_prompt(agent)

    def _reidentify_later():
        ctx.sleep(_REIDENTIFY_DELAY_S)
        tmux.inject(target, init_msg)

    ctx.background(_reidentify_later)
    return (f"{glyph} /compact → {ctx.session}:{agent} · 已让 agent 自压缩上下文 · "
            f"{int(_REIDENTIFY_DELAY_S)}s 后自动重注 identity")


def _handle_stop(args: str, ctx: SlashContext) -> str:
    if not args.strip():
        return "用法: /stop <agent>\n例: /stop worker_cc（送 C-c 中断当前动作）"
    agent = args.strip().split()[0]
    if (warn := _bad_agent(agent, ctx)):
        return warn
    ok = tmux.send_keys(tmux.Target(ctx.session, agent), "C-c")
    glyph = "✅" if ok else "❌"
    return f"{glyph} /stop → {ctx.session}:{agent} · 已送 C-c"


def _handle_clear(args: str, ctx: SlashContext) -> str:
    if not args.strip():
        return ("用法: /clear <agent>\n"
                "例: /clear worker_cc（清上下文 + 重新入职 init_msg，相当于 rehire）\n"
                "⚠️ 会丢 agent 当前会话记忆，谨慎用")
    agent = args.strip().split()[0]
    if (warn := _bad_agent(agent, ctx)):
        return warn
    target = tmux.Target(ctx.session, agent)
    if not tmux.inject(target, "/clear"):
        return f"❌ /clear → {ctx.session}:{agent} · 送 /clear 失败"
    ctx.sleep(2.0)
    if not tmux.inject(target, identity.init_prompt(agent)):
        return f"⚠️ /clear → {ctx.session}:{agent} · /clear 已送但 init_msg 重注入失败"
    return f"✅ /clear → {ctx.session}:{agent} · 已 /clear + 重新入职 init_msg"


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
}
# 9 chat slash commands: /help /team /health /usage /tmux /send
# /compact /stop /clear. Memory commands (`claudeteam recall` /
# `forget` / `remember`) live only as agent-pane CLIs, not chat slash.


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
