"""Slash-command dispatch — zero-LLM router-level handlers.

When the chat receives a message starting with `/`, `feishu/router.py`
emits `Decision(Action.SLASH, text=raw_text)`. `feishu/deliver.py` calls
`dispatch(text, ctx)` here, gets a string reply, and posts it back to
the chat as a bot message. **No worker pane is touched, no LLM runs.**

Supported commands (mirrors the old branch's contract 1:1):

    /help                    list commands
    /team                    `claudeteam team` output
    /health                  `claudeteam health` output
    /usage [view]            `claudeteam usage [--view <view>]` output
    /tmux <agent> [N]        last N (default 10) lines of agent's pane
    /send <agent> <msg>      tmux send-keys + Enter into agent's pane
    /compact <agent>         inject literal "/compact" so agent self-compacts
    /stop <agent>            send Ctrl-C to agent's pane
    /clear <agent>           inject "/clear" + re-init prompt to reset agent

Commands that need an agent name validate against the team agent set.
Commands that read pane text use `tmux capture-pane`. Commands that
shell out to `claudeteam` invoke the same in-tree CLI a worker would
(via `subprocess.run`), so output matches what an operator sees on the
host shell.
"""
from __future__ import annotations

import re
import shutil
import subprocess
import threading
import time
from collections import Counter
from dataclasses import dataclass
from datetime import datetime
from typing import Callable

from claudeteam.agents import identity
from claudeteam.feishu import pane_state
from claudeteam.runtime import tmux


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


_HELP_TEXT = """🆘 ClaudeTeam 斜杠命令（router 直接处理，零 LLM 消耗）

/help                    → 本帮助
/team                    → 所有员工 tmux 实时状态（emoji + 汇总）
/health                  → 部署 health 快照（路径/binaries/proxy/tmux/daemons）
/usage [view]            → claude-code 用量（ccusage 包装）
/tmux [agent] [lines]    → capture-pane 抓窗口（默认第一个 agent / 10 行）
/send <agent> <msg>      → 直接 tmux send-keys 注入消息到 agent 窗口
/compact [agent]         → 群聊无参=压缩第一个 agent；带参压缩指定 agent
/stop <agent>            → 送 C-c 到 agent 窗口（中断当前动作）
/clear <agent>           → 送 /clear + 重新入职 init prompt"""


def _handle_help(text: str, ctx: SlashContext) -> str | None:
    if not re.fullmatch(r"/help\s*", text):
        return None
    return _HELP_TEXT


def _handle_team(text: str, ctx: SlashContext) -> str | None:
    """Capture each agent's pane, classify state, format as table.

    Output mirrors the old branch's /team text block:
      👥 /team — 员工实时状态 (2026-05-03 09:30)
        💤 manager       idle
        🔄 worker_cc     working 1m 12s
        ⚠️  worker_codex  awaiting permission
        🛑 worker_kimi   CLI not running (bash)
      汇总: 4 agents · 💤 1 / 🔄 1 / ⚠️ 1 / 🛑 1
    """
    if not re.fullmatch(r"/team\s*", text):
        return None
    now_str = ctx.now().strftime("%Y-%m-%d %H:%M")
    rows = []
    tally: Counter[str] = Counter()
    for agent in ctx.team_agents:
        target = tmux.Target(ctx.session, agent)
        try:
            buf = tmux.capture_pane(target, lines=80)
        except Exception:
            buf = ""
        emoji, brief = pane_state.parse(buf)
        rows.append((agent, emoji, brief))
        tally[emoji] += 1

    name_w = max((len(a) for a, *_ in rows), default=8)
    lines = [f"👥 /team — 员工实时状态 ({now_str})", ""]
    for agent, emoji, brief in rows:
        lines.append(f"  {emoji} {agent.ljust(name_w)}  {brief}")
    lines.append("")
    total = sum(tally.values())
    summary = " / ".join(f"{k} {v}" for k, v in tally.most_common())
    lines.append(f"汇总: {total} agents · {summary}")
    return "\n".join(lines)


def _handle_health(text: str, ctx: SlashContext) -> str | None:
    """Wrap `claudeteam health` output with a header + Beijing-time stamp."""
    if not re.fullmatch(r"/health\s*", text):
        return None
    now_str = ctx.now().strftime("%Y-%m-%d %H:%M")
    out = _shell(ctx, ["claudeteam", "health"], timeout=60)
    return f"🩺 /health — 部署快照 ({now_str})\n\n{out}"


def _handle_usage(text: str, ctx: SlashContext) -> str | None:
    m = re.fullmatch(r"/usage(?:\s+(\S+))?\s*", text)
    if not m:
        return None
    now_str = ctx.now().strftime("%Y-%m-%d %H:%M")
    argv = ["claudeteam", "usage"]
    view = m.group(1) or "daily"
    if m.group(1):
        argv += ["--view", m.group(1)]
    out = _shell(ctx, argv, timeout=120)
    return f"📊 /usage ({view}) ({now_str})\n\n{out}"


def _handle_tmux(text: str, ctx: SlashContext) -> str | None:
    m = re.fullmatch(r"/tmux(?:\s+([A-Za-z0-9_\-]+))?(?:\s+(\d+))?\s*", text)
    if not m:
        return None
    agent = m.group(1) or (ctx.team_agents[0] if ctx.team_agents else "manager")
    lines = int(m.group(2)) if m.group(2) else 10
    lines = max(1, min(lines, _MAX_TMUX_LINES))
    if agent not in ctx.agent_set:
        return f"⚠️ 未知 agent: `{agent}`（合法名: {sorted(ctx.agent_set)}）"
    target = tmux.Target(ctx.session, agent)
    body = tmux.capture_pane(target, lines=lines)
    body = body.rstrip() or "(窗口为空)"
    return f"📺 {ctx.session}:{agent} 最后 {lines} 行\n\n{body}"


def _bad_agent(agent: str, ctx: SlashContext) -> str | None:
    """Common name-validation for /send, /compact, /stop, /clear. Returns
    a Chinese warning string on failure, None when the name is valid."""
    if not _AGENT_NAME_RE.fullmatch(agent):
        return f"⚠️ 非法 agent 名: `{agent}`"
    if agent not in ctx.agent_set:
        return f"⚠️ 未知 agent: `{agent}`（合法名: {sorted(ctx.agent_set)}）"
    return None


def _handle_send(text: str, ctx: SlashContext) -> str | None:
    if re.fullmatch(r"/send\s*", text):
        return "用法: /send <agent> <message>\n例: /send worker_cc \"看一下 README\""
    m = re.match(r"^/send\s+(\S+)\s+(.+)$", text, re.DOTALL)
    if not m:
        if re.match(r"^/send\s+\S+\s*$", text):
            return "用法: /send <agent> <message>（缺少消息内容）"
        return None
    agent, msg = m.group(1).strip(), m.group(2).strip()
    if (warn := _bad_agent(agent, ctx)):
        return warn
    target = tmux.Target(ctx.session, agent)
    ok = tmux.inject(target, msg)
    glyph = "✅" if ok else "❌"
    return f"{glyph} /send → {ctx.session}:{agent}\n内容: {msg}"


_REIDENTIFY_DELAY_S = 45.0   # rough upper bound for claude-code /compact


def _handle_compact(text: str, ctx: SlashContext) -> str | None:
    """Send /compact to agent's pane, then schedule a background
    re-identify so the agent reloads its identity.md after compaction
    settles (Round B.2 — post-compact identity reread)."""
    m = re.fullmatch(r"/compact(?:\s+(\S+))?\s*", text)
    if not m:
        return None
    agent = (m.group(1) or (ctx.team_agents[0] if ctx.team_agents else "manager")).strip()
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


def _handle_stop(text: str, ctx: SlashContext) -> str | None:
    if re.fullmatch(r"/stop\s*", text):
        return "用法: /stop <agent>\n例: /stop worker_cc（送 C-c 中断当前动作）"
    m = re.match(r"^/stop\s+(\S+)\s*$", text)
    if not m:
        return None
    agent = m.group(1).strip()
    if (warn := _bad_agent(agent, ctx)):
        return warn
    target = tmux.Target(ctx.session, agent)
    ok = tmux.send_keys(target, "C-c")
    glyph = "✅" if ok else "❌"
    return f"{glyph} /stop → {ctx.session}:{agent} · 已送 C-c"


def _handle_clear(text: str, ctx: SlashContext) -> str | None:
    if re.fullmatch(r"/clear\s*", text):
        return ("用法: /clear <agent>\n"
                "例: /clear worker_cc（清上下文 + 重新入职 init_msg，相当于 rehire）\n"
                "⚠️ 会丢 agent 当前会话记忆，谨慎用")
    m = re.match(r"^/clear\s+(\S+)\s*$", text)
    if not m:
        return None
    agent = m.group(1).strip()
    if (warn := _bad_agent(agent, ctx)):
        return warn
    target = tmux.Target(ctx.session, agent)
    if not tmux.inject(target, "/clear"):
        return f"❌ /clear → {ctx.session}:{agent} · 送 /clear 失败"
    ctx.sleep(2.0)
    if not tmux.inject(target, identity.init_prompt(agent)):
        return f"⚠️ /clear → {ctx.session}:{agent} · /clear 已送但 init_msg 重注入失败"
    return f"✅ /clear → {ctx.session}:{agent} · 已 /clear + 重新入职 init_msg"


# Dispatch order matters — first matching handler wins. Ordered by likely
# call frequency (status reads ahead of pane mutations).
_HANDLERS: tuple[Callable[[str, SlashContext], str | None], ...] = (
    _handle_help,
    _handle_team,
    _handle_health,
    _handle_usage,
    _handle_tmux,
    _handle_send,
    _handle_compact,
    _handle_stop,
    _handle_clear,
)


def is_slash_command(text: str) -> bool:
    """True if `text` is a recognised slash command. Pure check, no I/O."""
    if not text or not text.lstrip().startswith("/"):
        return False
    stripped = text.strip()
    # Cheap detection: does any handler claim it?
    fake_ctx = SlashContext(team_agents=[], session="")
    return any(h(stripped, fake_ctx) is not None for h in _HANDLERS)


def dispatch(text: str, ctx: SlashContext) -> str:
    """Run the first matching handler against `text`. Returns the reply
    string (always a string — caller posts it directly to chat). Unknown
    slash commands get a "use /help" suggestion."""
    if not text:
        return "⚠️ empty slash command"
    stripped = text.strip()
    for handler in _HANDLERS:
        try:
            reply = handler(stripped, ctx)
        except Exception as e:
            return f"⚠️ slash handler error: {e}"
        if reply is not None:
            return reply
    return f"⚠️ 未知斜杠命令: `{stripped}` — 试 /help 看支持的命令清单"
