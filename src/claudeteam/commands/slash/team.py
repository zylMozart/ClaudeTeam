"""Handlers for /team, /stop, /clear slash commands."""
from __future__ import annotations

import json
import re
import subprocess
from collections import defaultdict
from typing import Callable

from .context import SlashContext
from claudeteam.commands.team import parse_agent_state  # noqa: F401 (re-exported)
from claudeteam.runtime.agent_state import classify as classify_agent_state


def _run(cmd, timeout=5):
    try:
        return subprocess.run(cmd, capture_output=True, text=True, timeout=timeout)
    except Exception as e:
        class R:
            returncode = -1
            stdout = ""
            stderr = str(e)
        return R()


def parse_state_fallback(buf: str):
    if not buf:
        return ("⬜", "无窗口")
    low = buf.lower()
    tail_lines = [line for line in buf.splitlines() if line.strip()]
    tail = tail_lines[-1] if tail_lines else ""
    if re.search(r"root@[0-9a-f]+:[^#]*#\s*$", tail):
        return ("🛑", "Claude Code 未运行（bash）")
    if "hit your limit" in low:
        return ("⛔", "quota 超限")
    if "do you want to proceed" in low or re.search(r"❯\s*\d\.", buf):
        return ("⚠️", "等权限")
    if "compacting conversation" in low or "compacting…" in low:
        return ("🗜️", "压缩中")
    if "esc to interrupt" in low:
        m = re.search(r"\((\d+m\s*\d+s|\d+s)(?:\s*·[^)]*)?\)", buf)
        return ("🔄", f"工作中 {m.group(1) if m else ''}".strip())
    if "manifesting" in low:
        return ("🔄", "思考中")
    if re.search(r"⏵⏵\s*bypass permissions", buf) or "new task?" in low:
        return ("💤", "idle")
    return ("🔘", tail.strip()[:40])


def parse_state(buf: str, agent: str | None = None, session: str | None = None,
                classify_fn: Callable[[str, str], object] = classify_agent_state):
    if agent and session:
        try:
            state = classify_fn(agent, session)
            if state.code != "no_window" or not buf:
                return (state.emoji, state.brief)
        except Exception:
            pass
    return parse_state_fallback(buf)


def _load_agent_models(project_root) -> dict:
    try:
        data = json.loads((project_root / "team.json").read_text())
    except Exception:
        return {}
    return {name: (info or {}).get("model", "") for name, info in (data.get("agents") or {}).items()}


def _build_team_card(sections: list, tally: dict, now: str) -> dict:
    elements = []
    for idx, (label, rows) in enumerate(sections):
        if idx > 0:
            elements.append({"tag": "hr"})
        elements.append({"tag": "div", "text": {"tag": "lark_md", "content": f"**{label}**"}})
        i = 0
        while i < len(rows):
            chunk = rows[i:i + 3]
            cols = []
            for row in chunk:
                agent, emoji, brief = row[:3]
                model = row[3] if len(row) > 3 else ""
                model_line = f"\n<font color='grey'>model `{model}`</font>" if model else ""
                cell = f"{emoji} **{agent}**\n<font color='grey'>{brief or '-'}</font>{model_line}"
                cols.append({"tag": "column", "width": "weighted", "weight": 1,
                             "elements": [{"tag": "markdown", "content": cell}]})
            while len(cols) < 3:
                cols.append({"tag": "column", "width": "weighted", "weight": 1,
                             "elements": [{"tag": "markdown", "content": " "}]})
            elements.append({"tag": "column_set", "flex_mode": "none",
                             "background_style": "default", "columns": cols})
            i += 3

    total = sum(tally.values())
    summary = " / ".join(f"{k} {v}" for k, v in tally.items() if v)
    elements.append({"tag": "hr"})
    elements.append({"tag": "div", "text": {"tag": "lark_md",
                                             "content": f"**汇总**：{total} agents · {summary}"}})
    return {
        "config": {"wide_screen_mode": True},
        "header": {"template": "blue",
                   "title": {"tag": "plain_text", "content": f"👥 /team · {now} 北京时间"}},
        "elements": elements,
    }


def _list_windows_local(session: str, run_fn=_run):
    r = run_fn(["tmux", "list-windows", "-t", session, "-F", "#{window_name}"])
    if r.returncode != 0:
        return []
    return [w.strip() for w in r.stdout.splitlines() if w.strip()]


def _containers(run_fn=_run):
    r = run_fn(["sudo", "-n", "docker", "ps", "--format", "{{.Names}}"])
    if r.returncode != 0:
        return []
    return [n.strip() for n in r.stdout.splitlines() if n.strip().startswith("claudeteam-")]


def _container_window_map(cname: str, agent_set: frozenset, run_fn=_run) -> dict:
    r = run_fn(["sudo", "-n", "docker", "exec", cname, "tmux",
                "list-windows", "-a", "-F", "#{session_name}:#{window_name}"])
    if r.returncode != 0:
        return {}
    out = {}
    for line in r.stdout.splitlines():
        sess, _, win = line.partition(":")
        if win in agent_set and win not in out:
            out[win] = f"{sess}:{win}"
    return out


def build_team_response(
    agents: list[str],
    session: str,
    now: str,
    *,
    agent_set: frozenset | None = None,
    run_fn=_run,
    classify_fn: Callable[[str, str], object] = classify_agent_state,
) -> dict:
    agent_set = frozenset(agents) if agent_set is None else agent_set
    sections = []
    text_lines = [f"👥 /team — 员工实时状态 ({now} 北京时间)\n"]
    tally = defaultdict(int)

    rows = []
    text_lines.append(f"[本机 {session}]")
    host_windows = _list_windows_local(session, run_fn)
    for agent in agents:
        if agent not in host_windows:
            continue
        r = run_fn(["tmux", "capture-pane", "-t", f"{session}:{agent}", "-p"])
        buf = r.stdout if r.returncode == 0 else ""
        emoji, brief = parse_state(buf, agent, session, classify_fn)
        rows.append((agent, emoji, brief))
        text_lines.append(f"  {emoji} {agent:<10} {brief}")
        tally[emoji] += 1
    if rows:
        sections.append((f"本机 {session}", rows))

    for cname in _containers(run_fn):
        short = cname.replace("claudeteam-", "").replace("-team-1", "")
        wmap = _container_window_map(cname, agent_set, run_fn)
        rows = []
        text_lines.append(f"\n[容器 {short}]")
        for agent in agents:
            target = wmap.get(agent)
            if not target:
                continue
            r = run_fn(["sudo", "-n", "docker", "exec", cname, "tmux", "capture-pane", "-t", target, "-p"])
            buf = r.stdout if r.returncode == 0 else ""
            emoji, brief = parse_state_fallback(buf)
            rows.append((agent, emoji, brief))
            text_lines.append(f"  {emoji} {agent:<10} {brief}")
            tally[emoji] += 1
        if rows:
            sections.append((f"容器 {short}", rows))

    total = sum(tally.values())
    summary = " / ".join(f"{k} {v}" for k, v in tally.items() if v)
    text_lines.append(f"\n汇总：{total} agents · {summary}")
    return {"text": "\n".join(text_lines), "card": _build_team_card(sections, tally, now)}


def handle_team(text: str, ctx: SlashContext) -> dict | None:
    if not re.fullmatch(r"/team\s*", text):
        return None
    now_str = ctx.now_bj().strftime("%Y-%m-%d %H:%M 北京时间")
    models = _load_agent_models(ctx.project_root)
    sections = []
    for agent in ctx.team_agents:
        buf = ctx.capture_pane(agent)
        emoji, label = parse_agent_state(buf)
        sections.append({"agent": agent, "emoji": emoji, "label": label, "model": models.get(agent, "")})
    text_lines = [f"  {s['emoji']} {s['agent']}: {s['label']} · model {s['model']}" if s.get("model") else f"  {s['emoji']} {s['agent']}: {s['label']}" for s in sections]
    text_body = "🏢 团队状态 @ " + now_str + "\n" + "\n".join(text_lines)
    card_rows = [(s["agent"], s["emoji"], s["label"], s.get("model", "")) for s in sections]
    tally = defaultdict(int)
    for row in card_rows:
        tally[row[1]] += 1
    return {"text": text_body, "card": _build_team_card([("团队", card_rows)], tally, now_str)}
