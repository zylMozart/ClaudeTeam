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
    /recall <agent> [N] [--kind K]     card with agent's recent memory (R95+R108)
    /forget <agent> [--kind K] --yes   wipe agent memory; --yes gated (R112)
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

Internal helpers worth knowing:
    _pop_kind_arg(parts, cmd)               R138: shared `--kind K` extractor
                                            for /recall + /forget — order-
                                            agnostic, returns (parts, kind, err)
    _handle_team's lazy probe               R144: one `load_team()` per /team
                                            render (was N for N agents — each
                                            agent_config call re-reads disk).

Removed:
    R137 — `kv_card` (never had a production caller)
    R143 — `is_slash_command` (no production caller; router uses raw
            `/`-prefix detection, dispatch surfaces unknown commands)
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
from claudeteam.store import memory
from claudeteam.util import fmt_time_ms


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
_MAX_TMUX_LINES = 2000
_REIDENTIFY_DELAY_S = 45.0   # rough upper bound for claude-code /compact


def _default_agent(ctx: SlashContext) -> str:
    return ctx.team_agents[0] if ctx.team_agents else "manager"


def _bad_agent(agent: str, ctx: SlashContext) -> str | None:
    """Return a Chinese warning string if `agent` is unknown, else None."""
    if not _AGENT_NAME_RE.fullmatch(agent):
        return f"⚠️ 非法 agent 名: `{agent}`"
    if agent not in ctx.agent_set:
        return f"⚠️ 未知 agent: `{agent}`（合法名: {sorted(ctx.agent_set)}）"
    return None


def _pop_kind_arg(parts: list[str], cmd: str) -> tuple[list[str], str, str | None]:
    """Strip an optional `--kind K` pair from `parts`.

    Round-138: `/recall` and `/forget` both accept `--kind K` anywhere in
    the arg list. The extraction is identical 7-line state mutation —
    pulled here so each handler stays a one-liner. Returns
    `(remaining_parts, kind_or_empty, error_or_None)`. `error` is set
    only when `--kind` appeared without a following value; handlers
    return it directly. `cmd` only feeds the error string so the
    warning still names the slash command (e.g. `/recall: --kind …`).
    """
    if "--kind" not in parts:
        return parts, "", None
    idx = parts.index("--kind")
    if idx + 1 >= len(parts):
        return parts, "", f"⚠️ {cmd}: --kind needs a value (e.g. --kind decision)"
    kind = parts[idx + 1]
    return parts[:idx] + parts[idx + 2:], kind, None


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
/recall <agent> [N] [--kind K] → 看 agent 最近 N 条 memory（可按 kind 筛）
/forget <agent> [--kind K] --yes → 清 agent memory（全清或 kind 切片，必须带 --yes）
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
    # R158: lazy agents now come from ctx.lazy_agents, pre-computed at
    # daemon startup. Was R144: one `load_team()` per /team event.
    # Now: zero — every chat-side /team card render hits the disk
    # exactly 0 times for config (capture_pane is the only per-agent
    # I/O left, and that's a tmux subprocess, not a config read).
    lazy_agents = ctx.lazy_agents

    rows = []
    tally: Counter[str] = Counter()
    for agent in ctx.team_agents:
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


def _fmt_mem(b: int) -> str:
    """Bytes → GB/MB/KB string. Local mirror of server_metrics._fmt_mem
    so card builders don't have to import the runtime collector for
    formatting alone."""
    if b >= 1024**3:
        return f"{b/1024**3:.2f} GB"
    if b >= 1024**2:
        return f"{b/1024**2:.0f} MB"
    if b >= 1024:
        return f"{b/1024:.0f} KB"
    return f"{b} B"


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
                f"**{_fmt_mem(mem['used'])} / {_fmt_mem(mem['total'])}"
                f" ({mem['pct']}%)**</font>\n<font color='grey'>"
                f"可用 {_fmt_mem(mem['available'])} · Swap "
                f"{_fmt_mem(mem['swap']['used'])}/{_fmt_mem(mem['swap']['total'])}"
                f"</font>")
    disk_cell = ("**磁盘**\n<font color='grey'>无数据</font>" if not disk else
                 f"**磁盘** `{disk['mount']}`\n"
                 f"<font color='{load_color(disk['pct'])}'>"
                 f"**{_fmt_mem(disk['used'])} / {_fmt_mem(disk['total'])}"
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
            f"**容器内存合计**\n**{_fmt_mem(total_mem)}**\n"
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
                f"CPU `{a['cpu']:.1f}%` · Mem `{_fmt_mem(a['mem'])}`\n"
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
    data = server_metrics.collect_server_load(
        agent_set=frozenset(ctx.team_agents),
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


_CCUSAGE_TOTAL_RE = re.compile(
    r"(?:Total|总计|合计)\s*:?\s*\$?\s*([\d.]+)", re.IGNORECASE)
_CCUSAGE_DOLLAR_LINE_RE = re.compile(r"\$\s*([\d.]+)")
# Priority-ordered hint phrases. The summariser walks this in order and
# returns the first matching LINE — so a real Error: line wins over
# the npm WARN noise that typically precedes it.
_CCUSAGE_ERROR_HINTS = (
    "No valid Claude data directories found",
    "Cannot find module",
    "MODULE_NOT_FOUND",
    "Error:",
    "npm error",
    "Unsupported engine",
)


def _extract_ccusage_summary(output: str) -> dict | None:
    """Pull a small structured summary out of ccusage stdout.

    ccusage prints either a table (daily/monthly view) or a single
    Total line. Either way we want to render one or two compact metric
    rows in the card body, not a 30-line code-block dump.

    Returns `{total: "$X.YZ"}` or None if no money-shaped pattern was
    found.
    """
    if not output:
        return None
    m = _CCUSAGE_TOTAL_RE.search(output)
    if m:
        return {"total": f"${m.group(1)}"}
    # Fallback: grab the LAST `$X.YZ` token in the output (often the
    # rightmost cell of the totals row).
    matches = _CCUSAGE_DOLLAR_LINE_RE.findall(output)
    if matches:
        return {"total": f"${matches[-1]}"}
    return None


def _summarise_ccusage_error(output: str) -> str:
    """Boil ccusage's multi-line failure (npm WARN ... + Node stack
    trace) down to one operator-readable line. Keeps the actual error
    message and drops the surrounding noise.

    Walks `_CCUSAGE_ERROR_HINTS` in priority order so a real
    `Error: No valid Claude data directories found` wins over the
    `npm WARN EBADENGINE Unsupported engine` noise that typically
    precedes it.
    """
    if not output:
        return "ccusage 无输出"
    low = output.lower()
    for hint in _CCUSAGE_ERROR_HINTS:
        if hint.lower() in low:
            for line in output.splitlines():
                if hint.lower() in line.lower():
                    return line.strip()[:200]
    # Fallback: first non-WARN line under 200 chars
    for line in output.splitlines():
        s = line.strip()
        if s and not s.lower().startswith(("npm warn", "warning:")):
            return s[:200]
    return output.splitlines()[0].strip()[:200] if output.strip() else "(空)"


def _handle_usage(args: str, ctx: SlashContext) -> dict:
    """`/usage [view]` — token / credit consumption snapshot card.

    R167: ports `feat/messaging-fixes-block1` / `main` shape — purple
    header, **📊 Claude Code (ccusage)** section with column_set 2
    (label / colored metric), **📦 其他 CLI** section listing
    codex/kimi/qwen/gemini per-cli notes (since rebuild has no usage
    adapter for those — main's `inspect_cli` lives outside this
    branch). ccusage failures are condensed to a one-line red font
    summary instead of dumping 30 lines of npm WARN.

    Was R164: plain text dump of `claudeteam usage` shell-out — boss
    flagged as "破衣服" vs /health's "西装"; this matches the latter
    so they look consistent in chat.
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
        data = {"view": view, "claude_code": None, "other_clis": []}

    elements: list = []
    cc = data.get("claude_code")
    if cc is not None:
        elements.append({"tag": "markdown",
                          "content": "**📊 Claude Code (ccusage)**"})
        if cc.get("ok"):
            summary = _extract_ccusage_summary(cc.get("output", ""))
            if summary:
                elements.append(column_set_2(
                    "**Total**",
                    f"<font color='blue'>**{summary['total']}**</font> "
                    f"· view `{view}`"))
            else:
                elements.append(column_set_2(
                    "**Status**",
                    f"<font color='grey'>ccusage 跑通但没匹配到金额行</font>"))
        else:
            err_brief = _summarise_ccusage_error(cc.get("output", ""))
            elements.append(column_set_2(
                "**Status**",
                f"<font color='red'>ccusage 失败</font> · {err_brief}"))
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

    if not cc and not other:
        elements.append({"tag": "markdown",
                          "content": "<font color='grey'>(无数据)</font>"})

    if elements and elements[-1].get("tag") == "hr":
        elements.pop()

    elements.append({"tag": "markdown",
                      "content": (f"<font color='grey'>采集 "
                                   f"{beijing_stamp(ctx.now)} · "
                                   f"data source `claudeteam usage --json`"
                                   f"</font>")})

    color = "red" if (cc and not cc.get("ok")) else "purple"
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
    raw_lines = int(parts[1]) if len(parts) >= 2 and parts[1].isdigit() else 10
    n_lines = max(1, min(raw_lines, _MAX_TMUX_LINES))
    if agent not in ctx.agent_set:
        return f"⚠️ 未知 agent: `{agent}`（合法名: {sorted(ctx.agent_set)}）"
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


_RECALL_DEFAULT_LIMIT = 10
_RECALL_MAX_LIMIT = 50


def _handle_recall(args: str, ctx: SlashContext) -> str | dict:
    """`/recall <agent> [N] [--kind K]` — boss-from-chat path to inspect any
    agent's durable memory. Default N = 10 entries; max 50 so the card
    body fits Feishu's render window.

    Round-95: complementary to install-hooks' /recall (fired from inside
    an agent's pane and shells through `claudeteam recall`).
    Round-108: optional `--kind K` filter — boss types
    `/recall worker_cc --kind blocker 5` to see only that agent's
    blocker entries. Filter goes through memory's full window, then
    trims to N matches (so a hot agent with many notes still surfaces
    rare decisions)."""
    parts = args.split()
    if not parts:
        return ("用法: /recall <agent> [N] [--kind K]\n"
                f"例: /recall manager 5 --kind decision\n"
                f"默认 N={_RECALL_DEFAULT_LIMIT}, 最多 {_RECALL_MAX_LIMIT}; "
                f"K 约定: {memory.kinds_sorted()}")

    # Pull --kind out before positional parsing so order doesn't matter
    # (`--kind blocker worker_cc 5` and `worker_cc 5 --kind blocker`
    # both work). R138: shared with /forget.
    parts, kind_filter, err = _pop_kind_arg(parts, "/recall")
    if err:
        return err

    if not parts:
        return "用法: /recall <agent> [N] [--kind K]"
    agent = parts[0]
    if (warn := _bad_agent(agent, ctx)):
        return warn
    raw_n = parts[1] if len(parts) >= 2 else ""
    if raw_n and not raw_n.isdigit():
        return f"⚠️ /recall {agent} <N>: N 必须是正整数（got `{raw_n}`）"
    n = max(1, min(int(raw_n) if raw_n else _RECALL_DEFAULT_LIMIT,
                   _RECALL_MAX_LIMIT))

    if kind_filter and kind_filter not in memory.KNOWN_KINDS:
        # Soft inline note — render in the card subtitle so boss sees
        # the typo guard, but proceed with the filter (matches CLI behavior).
        kind_warn = f" _(注: kind={kind_filter!r} 不在约定 {memory.kinds_sorted()})_"
    else:
        kind_warn = ""

    rows = memory.list_recent_filtered(agent, kind=kind_filter, limit=n)

    stamp = beijing_stamp(ctx.now)
    title_filter = f" / kind={kind_filter}" if kind_filter else ""
    if not rows:
        return simple_card(
            f"🧠 /recall {agent}{title_filter} — 无记忆 ({stamp})",
            f"_{agent} 在此过滤下没有任何 memory entry。{kind_warn}_"
            if kind_filter
            else f"_{agent} 还没写过任何 memory entry。试 `claudeteam "
                 f"remember {agent} ...` 写一条。_",
            color="grey",
        )
    body_lines = []
    if kind_warn:
        body_lines.append(kind_warn.strip())
        body_lines.append("")
    for r in rows:
        ts = fmt_time_ms(r.get("created_at", 0))
        kind = r.get("kind", "?")
        content = r.get("content", "")
        ref = r.get("ref", "")
        suffix = f" (ref={ref})" if ref else ""
        body_lines.append(f"`[{ts}]` **[{kind}]** {content}{suffix}")
    return simple_card(
        f"🧠 /recall {agent}{title_filter} — 最近 {len(rows)} 条 ({stamp})",
        "\n".join(body_lines),
    )


def _handle_forget(args: str, ctx: SlashContext) -> str | dict:
    """`/forget <agent> [--kind K] --yes` — wipe agent memory from chat.

    Symmetric to /recall + remember CLI. Two flavours:
      /forget <agent> --yes               drops ALL entries
      /forget <agent> --kind K --yes      drops only K-kind entries

    Round-112 protective gate: refuses without `--yes` and shows a
    grey card with the exact `--yes` reissue string the operator
    should type. Stops a slip-of-the-keyboard from nuking accumulated
    context — operator has to commit the `--yes` token explicitly.
    """
    parts = args.split()
    if not parts:
        return ("用法: /forget <agent> [--kind K] --yes\n"
                f"例: /forget worker_cc --kind blocker --yes\n"
                f"K 约定: {memory.kinds_sorted()} (默认 = 全清)")

    yes = "--yes" in parts
    if yes:
        parts = [p for p in parts if p != "--yes"]
    parts, kind, err = _pop_kind_arg(parts, "/forget")
    if err:
        return err

    if not parts:
        return "用法: /forget <agent> [--kind K] --yes"
    agent = parts[0]
    if (warn := _bad_agent(agent, ctx)):
        return warn

    if not yes:
        # Confirmation gate. Render the exact reissue string so boss
        # can copy-paste it back without re-typing the parameters.
        reissue = f"/forget {agent}" + (f" --kind {kind}" if kind else "") + " --yes"
        target = f"{agent}'s {kind} memory" if kind else f"{agent}'s entire memory"
        return simple_card(
            f"⚠️ /forget {agent} — 确认前不会执行",
            f"准备清掉：**{target}**。\n\n"
            f"先用以下命令预览要丢什么：\n"
            f"```\n/recall {agent}" + (f" --kind {kind}" if kind else "") + "\n```\n"
            f"\n确认后再发：\n```\n{reissue}\n```",
            color="grey",
        )

    if kind and kind not in memory.KNOWN_KINDS:
        kind_warn = (f"\n\n_(注: kind={kind!r} 不在约定 "
                     f"{memory.kinds_sorted()})_")
    else:
        kind_warn = ""

    if kind:
        n = memory.clear_kind(agent, kind)
        if n == 0:
            body = f"_{agent} 在 kind={kind} 上没有任何 entry，无事可做。_{kind_warn}"
        else:
            body = f"已清掉 **{n}** 条 `{kind}` 类 memory entries。{kind_warn}"
    else:
        n = memory.clear(agent)
        if n == 0:
            body = f"_{agent} 的 memory 本来就是空的，无事可做。_"
        else:
            body = f"已清掉 **{n}** 条 memory entries（全部）。"

    color = "red" if n > 0 else "grey"
    return simple_card(
        f"🗑 /forget {agent}" + (f" --kind {kind}" if kind else "") + " — 完成",
        body,
        color=color,
    )


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
    "/recall": _handle_recall,  # round-95: boss-from-chat agent memory inspect
    "/forget": _handle_forget,  # round-112: boss-from-chat memory wipe (--yes gated)
    "/stop": _handle_stop,
    "/clear": _handle_clear,
}


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
