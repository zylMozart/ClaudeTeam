"""Handlers for /tmux, /send, /compact slash commands."""
from __future__ import annotations

import re
from .context import SlashContext

_TMUX_RE = re.compile(r"^/tmux(?:\s+([A-Za-z0-9_-]+))?(?:\s+(\d+))?\s*$")

MAX_LINES = 2000


def handle_tmux(text: str, ctx: SlashContext) -> str | None:
    m = _TMUX_RE.match(text)
    if not m:
        return None
    agent = m.group(1) or (ctx.team_agents[0] if ctx.team_agents else "manager")
    lines = int(m.group(2)) if m.group(2) else 10
    lines = max(1, min(lines, MAX_LINES))
    if agent not in ctx.agent_set:
        return f"⚠️ 未知 agent：`{agent}`"
    body = ctx.capture_pane(agent).rstrip() or "(窗口为空)"
    return f"=== {ctx.tmux_session}:{agent} 最后 {lines} 行 ===\n{body}"


def handle_send(text: str, ctx: SlashContext) -> str | None:
    if re.fullmatch(r"/send\s*", text):
        return "用法: /send <agent> <message>\n例: /send devops 马上停"
    m = re.match(r"^/send\s+(\S+)\s+(.+)$", text, re.DOTALL)
    if not m:
        if re.match(r"^/send\s+\S+\s*$", text):
            return "用法: /send <agent> <message>\n例: /send devops 马上停\n（缺少消息内容）"
        return None
    agent = m.group(1).strip()
    msg = m.group(2).strip()
    if not re.fullmatch(r"[A-Za-z0-9_-]+", agent):
        return f"⚠️ 非法 agent 名：`{agent}`"
    if agent not in ctx.agent_set:
        return f"⚠️ 未知 agent：`{agent}`\n白名单：{sorted(ctx.agent_set)}"
    ok = ctx.send_to_agent(ctx.tmux_session, agent, msg)
    return f"{'✅' if ok else '❌'} /send → {ctx.tmux_session}:{agent}\n内容：{msg}"


def handle_compact(text: str, ctx: SlashContext) -> str | None:
    m = re.fullmatch(r"/compact(?:\s+(\S+))?\s*", text)
    if not m:
        return None
    agent = (m.group(1) or (ctx.team_agents[0] if ctx.team_agents else "manager")).strip()
    if not re.fullmatch(r"[A-Za-z0-9_-]+", agent):
        return f"⚠️ 非法 agent 名：`{agent}`"
    if agent not in ctx.agent_set:
        return f"⚠️ 未知 agent：`{agent}`"
    ok = ctx.send_to_agent(ctx.tmux_session, agent, "/compact")
    return f"{'✅' if ok else '❌'} /compact → {ctx.tmux_session}:{agent}"
