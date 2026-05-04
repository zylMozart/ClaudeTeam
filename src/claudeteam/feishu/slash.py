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
from claudeteam.feishu.cards import beijing_stamp, fenced_block, simple_card
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


def _is_lazy(cfg, agent: str) -> bool:
    """Probe whether `agent` is configured `lazy: true` in team.json.

    Round-129: used by `_handle_team` to distinguish "agent's CLI down
    because the boss never sent a first message" (lazy, expected) from
    "agent's CLI died unexpectedly" (alarm). Returns False on any
    config lookup error — a missing-from-team.json agent is "not lazy"
    by default, so it'll still flag as unhealthy if its pane is dead.
    """
    try:
        return bool(cfg.agent_config(agent).get("lazy"))
    except Exception:
        return False


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
    # Pull team config once so we can tell which agents are intentionally
    # lazy. Falls back gracefully if config raises (unknown agent in
    # ctx.team_agents that isn't in team.json — shouldn't happen, but
    # don't crash the card render).
    try:
        from claudeteam.runtime import config as _cfg
        lazy_agents = {a for a in ctx.team_agents
                       if _is_lazy(_cfg, a)}
    except Exception:
        lazy_agents = set()

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


def _handle_health(args: str, ctx: SlashContext) -> dict:
    """Run `claudeteam health` and wrap its text output in a card.

    Round-81: was a plain text reply; now a card. Color signals overall
    state — `green` when no `❌` glyph appears in the output (the health
    report's `_BAD` marker is `❌`), `yellow` when one or more `❌` lines
    are present. Body fences the raw health text in a code block so
    indentation + glyph alignment carry through Feishu's lark_md
    rendering without getting collapsed.
    """
    out = _shell(ctx, ["claudeteam", "health"], timeout=60)
    # Health uses ❌ for hard fails and ⚠️ for warnings (see health.py
    # _BAD / _WARN). Either should flip the card off green so the boss
    # notices something's off without reading the body.
    color = "yellow" if ("❌" in out or "⚠️" in out) else "green"
    return simple_card(
        f"🩺 /health — 部署快照 {beijing_stamp(ctx.now)}",
        fenced_block(out),
        color=color,
    )


def _handle_usage(args: str, ctx: SlashContext) -> dict:
    """Run `claudeteam usage` and wrap its output in a card.

    Round-115: was plain text; now matches /team /health card style.
    Body fences raw output so the table-formatted ccusage rows
    (totals / per-day breakdown) survive Feishu's lark_md collapsing.
    """
    view_arg = args.strip().split()[0] if args.strip() else ""
    argv = ["claudeteam", "usage"]
    if view_arg:
        argv += ["--view", view_arg]
    view = view_arg or "daily"
    out = _shell(ctx, argv, timeout=120)
    return simple_card(
        f"📊 /usage ({view}) — {beijing_stamp(ctx.now)}",
        fenced_block(out),
        color="blue",
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

    if kind_filter:
        all_rows = memory.list_recent(agent, limit=memory._MAX_PER_AGENT)
        rows = [r for r in all_rows if r.get("kind") == kind_filter][-n:]
    else:
        rows = memory.list_recent(agent, limit=n)

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


def is_slash_command(text: str) -> bool:
    """True if `text`'s leading token is a recognised slash command."""
    if not text or not text.lstrip().startswith("/"):
        return False
    cmd, _ = _split_cmd(text)
    return cmd in _HANDLERS


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
