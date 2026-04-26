"""Handlers for /team, /stop, /clear slash commands."""
from __future__ import annotations

import re
from .context import SlashContext
from claudeteam.commands.team import parse_agent_state  # noqa: F401 (re-exported)


def handle_team(text: str, ctx: SlashContext) -> dict | None:
    if not re.fullmatch(r"/team\s*", text):
        return None
    now_str = ctx.now_bj().strftime("%Y-%m-%d %H:%M 北京时间")
    sections = []
    for agent in ctx.team_agents:
        buf = ctx.capture_pane(agent)
        emoji, label = parse_agent_state(buf)
        sections.append({"agent": agent, "emoji": emoji, "label": label})
    text_lines = [f"  {s['emoji']} {s['agent']}: {s['label']}" for s in sections]
    text_body = "🏢 团队状态 @ " + now_str + "\n" + "\n".join(text_lines)
    card = _build_team_card(sections, now_str)
    return {"text": text_body, "card": card}


def _build_team_card(sections: list, now: str) -> dict:
    rows = []
    for s in sections:
        rows.append({
            "tag": "div",
            "text": {
                "tag": "lark_md",
                "content": f"**{s['agent']}** {s['emoji']} {s['label']}"
            }
        })
    return {
        "schema": "2.0",
        "body": {
            "elements": [
                {"tag": "markdown", "content": f"🏢 **团队状态** @ {now}"},
                *rows,
            ]
        }
    }


# ── /stop ─────────────────────────────────────────────────────

def handle_stop(text: str, ctx: SlashContext) -> str | None:
    m = re.fullmatch(r"/stop\s+(\S+)\s*", text)
    if not m:
        if re.fullmatch(r"/stop\s*", text):
            return "用法: /stop <agent>"
        return None
    agent = m.group(1)
    if agent not in ctx.agent_set:
        return f"⚠️ 未知 agent：`{agent}`"
    # Ctrl+C delivered via send_to_agent with special sentinel
    ok = ctx.send_to_agent(ctx.tmux_session, agent, "\x03")
    return f"{'✅' if ok else '❌'} C-c → {ctx.tmux_session}:{agent}"


# ── /clear ────────────────────────────────────────────────────

def handle_clear(text: str, ctx: SlashContext) -> str | None:
    m = re.fullmatch(r"/clear\s+(\S+)\s*", text)
    if not m:
        if re.fullmatch(r"/clear\s*", text):
            return "用法: /clear <agent>"
        return None
    agent = m.group(1)
    if agent not in ctx.agent_set:
        return f"⚠️ 未知 agent：`{agent}`"
    ok = ctx.send_to_agent(ctx.tmux_session, agent, "/clear")
    return f"{'✅' if ok else '❌'} /clear → {ctx.tmux_session}:{agent}"
