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

Card-building helpers all live in `feishu/cards.py` (R136 consolidation):
    simple_card(title, body, color)         shape constructor
    beijing_stamp(now) -> str               R117: card-title timestamp suffix
                                            (callers pass `ctx.now`)
    fenced_block(text) -> str               R118: monospace lark_md fence

Removed:
    R137 — `kv_card` (never had a production caller)
    R143 — `is_slash_command` (no production caller; router uses raw
            `/`-prefix detection, dispatch surfaces unknown commands)
    R172.b — `/recall` + `/forget` slash dispatch (boss flagged 2026-05-04
              as not in main and not requested). CLI commands `claudeteam
              recall|forget` stay as agent-pane tools.
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
    rich_card, simple_card,
)
from claudeteam.runtime import tmux
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
    # R158: pre-computed at daemon startup so /team's lazy-pane probe
    # doesn't have to call `config.load_team()` per chat event. Empty
    # default keeps tests + cold-path callers (they construct their
    # own SlashContext in fakes) working without churn — the only
    # consequence of an empty set is that lazy agents render as 🛑
    # instead of ⏸, which is the pre-R129 behavior.
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

    Round-129: lazy agents (`team.json` `"lazy": true`) get the ⏸
    glyph instead of 🛑 — lazy is BY DESIGN, not a failure. R128 smoke
    caught the wart: yellow team header for a worker that's just
    waiting for its first message looks like an alarm.
    """
    # _live_agents reads config every call so claudeteam.toml edits
    # take effect on the next /team without a router restart.
    team_agents, _, lazy_agents, _ = _live_agents()

    rows = []
    tally: Counter[str] = Counter()
    for agent in team_agents:
        target = tmux.Target(ctx.session, agent)
        try:
            buf = tmux.capture_pane(target, lines=80)
        except Exception:
            buf = ""
        emoji, brief = pane_state.parse(buf)
        # Recognise lazy state: pane_state.parse returns 🛑 (Linux bash
        # prompt regex match) or 🔘 (tail-fallback / macOS shell with %
        # prompt) for "no CLI". Either is fine for an agent team.json
        # marks `lazy: true` — flip to ⏸ so the team-color check below
        # doesn't go yellow. R128 / R129 caught this on macOS host.
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
    # ❌ etc.), green otherwise. 💤 idle / 🔄 working / ⏸ lazy are healthy.
    healthy_glyphs = ("💤", "🔄", "⏸")
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

    R166 ports `feat/messaging-fixes-block1:slash/health.build_server_
    load_card`'s layout: 🖥️ 主机总览 (CPU / 内存 / 磁盘 column_set) →
    📦 容器总量 → 👤 员工细分 Top N (per-row column_set) → 🚨 异常告警 →
    note footer. Each section ends with an `hr` divider; final `hr` is
    pruned before the note.
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

    R166: completely re-shaped per boss-flagged "card 完全不行" feedback.
    Was a plain-text dump of `claudeteam health`'s deploy-checks; now
    matches `feat/messaging-fixes-block1` / `main` shape — host
    CPU/mem/disk + docker containers + per-agent process subtree
    rendered in column_set 3 grids with red/orange/green percentages
    and emoji section headings. Data comes from
    `runtime/server_metrics.collect_server_load`. Header template
    stays purple per the older branch.

    Falls back to a "无数据" cell per metric when the underlying
    `uptime` / `free` / `df` / `docker stats` / `ps` shell-out
    returns nothing — common inside the container on macOS Docker
    Desktop where some host commands aren't visible.
    """
    from claudeteam.runtime import server_metrics
    _, agent_set, _, _ = _live_agents()
    data = server_metrics.collect_server_load(
        agent_set=agent_set,
        session=ctx.session,
    )
    elements = _build_server_load_elements(data)
    # R166: v2 schema dropped the v1 `note` element ("cards of schema V2
    # no longer support this capability; unsupported tag note"); use a
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


# R173: dropped `_extract_ccusage_summary` / `_summarise_ccusage_error`
# (and their CCUSAGE regex constants). ccusage just returned cumulative
# cost ("Total: $X") which boss flagged as wrong — replaced by direct
# Anthropic OAuth /usage API call (real per-window utilization). See
# `commands/usage._query_cc_usage`.


def _remaining_color(remaining_pct: float) -> str:
    """R170: traffic-light header tint for kimi remaining%.
    ≤20 red / ≤50 orange / else green — mirrors main's
    `_remaining_pct_color`."""
    if remaining_pct <= 20:
        return "red"
    if remaining_pct <= 50:
        return "orange"
    return "green"


def _codex_section(cx: dict) -> list:
    """Render Codex section rows for `/usage` card.

    R173: shows real % consumed per limit window (5h / Weekly / etc),
    same traffic-light layout as Kimi. Boss flagged R170's earlier
    JWT-decode-only output as useless ("登录账号有屁用啊"). Now we
    shell out to `codex-cli-usage` (uv-installed in container) and
    render its lines as percent metrics. Plan stays as a top header
    line for context. ok=False → red Status line."""
    rows: list = [{"tag": "markdown", "content": "**🟦 Codex (ChatGPT OAuth)**"}]
    if not cx.get("ok"):
        rows.append(column_set_2(
            "**Status**",
            f"<font color='red'>Codex 用量读取失败</font> · {cx.get('note', '')}"))
        return rows
    plan = cx.get("plan") or "unknown"
    rows.append(column_set_2(
        "**Plan**", f"<font color='blue'>**{plan}**</font>"))
    metrics = cx.get("metrics") or []
    if not metrics:
        rows.append(column_set_2(
            "**Status**",
            f"<font color='grey'>codex-cli-usage 跑通但没返回 % 数据</font>"))
        return rows
    for m in metrics:
        color = _remaining_color(m["remaining_pct"])
        rows.append(column_set_2(
            f"**{m['label']}**",
            (f"<font color='{color}'>**剩余 {m['remaining_pct']}%**</font> "
             f"· 已用 {m['used_pct']}% · 重置 {m.get('reset', '')}")))
    return rows


def _kimi_section(km: dict) -> list:
    """Render Kimi section rows for `/usage` card.

    R170: queries `api.kimi.com/coding/v1/usages` — returns weekly +
    sliding-window quotas. Each metric becomes a column_set 2 row
    `label / <font color>剩余 X%</font> · 已用 Y%/Z` so traffic-light
    color matches the underlying remaining percentage."""
    rows: list = [{"tag": "markdown", "content": "**🟧 Kimi (api.kimi.com)**"}]
    if not km.get("ok"):
        rows.append(column_set_2(
            "**Status**",
            f"<font color='red'>Kimi API 失败</font> · {km.get('note', '')}"))
        return rows
    for m in km.get("metrics", []) or []:
        color = _remaining_color(m["remaining_pct"])
        rows.append(column_set_2(
            f"**{m['label']}**",
            (f"<font color='{color}'>**剩余 {m['remaining_pct']}%**</font> "
             f"· 已用 {m['used_pct']}% ({m['used']}/{m['limit']}) "
             f"· 重置 {m.get('reset_iso', '')}")))
    return rows


def _handle_usage(args: str, ctx: SlashContext) -> dict:
    """`/usage [view]` — token / credit consumption snapshot card.

    R170: per-CLI sections — Claude Code via ccusage, Codex via decoded
    `~/.codex/auth.json` JWT (plan + window), Kimi via `api.kimi.com/
    coding/v1/usages`. Each section is `**heading**` + column_set 2
    rows + hr separator. Mirrors `main`'s `build_usage_card` layout
    but stripped of the `inspect_cli` preflight machinery (rebuild
    keeps a leaner status surface — actual per-CLI failure lives in
    `commands/usage.py`'s probe functions and surfaces here as the
    section's red `Status` line).

    R167: ports `feat/messaging-fixes-block1` / `main` shape — purple
    header, ccusage failures condensed to one line.
    R164→R167 history: plain text → ccusage-only card → multi-CLI card.
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
                color = _remaining_color(m["remaining_pct"])
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

    Round-116: was plain text; now a blue card with code-fenced body
    so the monospace pane content (banners / spinners / box drawing)
    renders aligned. Empty pane gets a placeholder, mirroring the
    CLI `claudeteam peek`'s (R103) `(empty buffer for X)` line.

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


def _handle_compact(args: str, ctx: SlashContext) -> str:
    """Send /compact to agent's pane, then schedule a background
    re-identify so the agent reloads its identity.md after compaction
    settles (Round B.2 — post-compact identity reread)."""
    parts = args.split()
    agent = (parts[0] if parts else _default_agent(ctx)).strip()
    if (warn := _bad_agent(agent, ctx)):
        return warn
    target = tmux.Target(ctx.session, agent)
    ok = tmux.inject(target, "/compact")
    glyph = "✅" if ok else "❌"
    if not ok:
        return f"{glyph} /compact → {ctx.session}:{agent} · 已让 agent 自压缩上下文"
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
# R172.b: dropped /recall + /forget — boss flagged as "not in main and
# not requested". Memory CLI commands (`claudeteam recall|forget`) stay
# as agent-pane tools; only the chat slash dispatch entries went away.
# Matches main's exact 9-command surface: /help /team /health /usage
# /tmux /send /compact /stop /clear.


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
