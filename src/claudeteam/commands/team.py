"""共享 /team 解析 + 团队状态采集。

两处入口共用（都不走 LLM）：
    1) Claude Code UserPromptSubmit hook — .claude/hooks/team_intercept.py
    2) scripts/feishu_router.py — 飞书群消息入口
"""
import json
import os
import re

from claudeteam.runtime.tmux_utils import capture_pane as _capture_pane, is_agent_idle
from claudeteam.runtime.config import resolve_model_for_agent, resolve_thinking_for_agent
from claudeteam.cli_adapters import adapter_for_agent
from claudeteam.commands._team_io import load_team

_CMD_RE = re.compile(r"^/team\s*$")
_PROJECT_ROOT = os.path.join(os.path.dirname(__file__), "..", "..", "..", "..")


def parse(text: str):
    """匹配 /team, 返回 True 或 None。"""
    if not text:
        return None
    return True if _CMD_RE.match(text.strip()) else None


def parse_agent_state(buf: str) -> tuple[str, str]:
    """Classify tmux pane content → (emoji, label)."""
    if not buf:
        return ("❔", "无窗口")
    low = buf.lower()
    tail_lines = [l for l in buf.splitlines() if l.strip()]
    tail = tail_lines[-1] if tail_lines else ""
    if re.search(r"root@[0-9a-f]+:[^#]*#\s*$", tail):
        return ("🛑", "Claude Code 未运行（bash）")
    if "hit your limit" in low:
        return ("🔴", "超额度 / 被限速")
    if re.search(r"[⣾⣽⣻⢿⡿⣟⣯⣷◐◑◒◓]", tail):
        return ("⚡", "处理中")
    if re.search(r"thinking|running tool", low):
        return ("⚡", "处理中")
    if re.search(r"[>❯]\s*$", tail):
        return ("✅", "空闲等待输入")
    if re.search(r"do you want to proceed", low):
        return ("⏸️", "等待确认")
    return ("🔵", "运行中")


def collect_team_status(session=None):
    """采集每个 agent 的状态, 返回 list[dict]。"""
    team = load_team()
    if session is None:
        session = team.get("session", "ClaudeTeam")
    agents = team.get("agents", {})

    rows = []
    for name, info in agents.items():
        adapter = adapter_for_agent(name)
        try:
            model = resolve_model_for_agent(name)
        except Exception:
            model = "?"
        try:
            thinking = resolve_thinking_for_agent(name)
        except Exception:
            thinking = "?"

        cli = info.get("cli", "claude-code")
        role = info.get("role", name)

        pane = _capture_pane(session, name, lines=3)
        if pane is None:
            status = "offline"
        elif "\U0001f4a4 待 wake" in pane or "\U0001f4a4" in pane.split("\n")[-1]:
            status = "suspended \U0001f4a4"
        elif not is_agent_idle(session, name, adapter.busy_markers()):
            status = "busy"
        else:
            status = "idle"

        rows.append({
            "name": name, "role": role, "cli": cli,
            "model": model, "thinking": thinking, "status": status,
        })
    return rows


def format_table(rows):
    """格式化成文本表格。"""
    if not rows:
        return "(团队无 agent)"
    headers = ["Agent", "Role", "CLI", "Model", "Thinking", "Status"]
    widths = [len(h) for h in headers]
    for r in rows:
        vals = [r["name"], r["role"], r["cli"], r["model"],
                r["thinking"], r["status"]]
        for i, v in enumerate(vals):
            widths[i] = max(widths[i], len(str(v)))

    def _row(vals):
        return "  ".join(str(v).ljust(widths[i]) for i, v in enumerate(vals))

    lines = ["=== Team Status ==="]
    lines.append(_row(headers))
    lines.append(_row(["\u2500" * w for w in widths]))
    for r in rows:
        lines.append(_row([r["name"], r["role"], r["cli"], r["model"],
                           r["thinking"], r["status"]]))
    return "\n".join(lines)


def collect_and_format(session=None):
    rows = collect_team_status(session)
    return format_table(rows)
