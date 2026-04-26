"""共享 /team 解析 + 团队状态采集。

两处入口共用（都不走 LLM）：
    1) Claude Code UserPromptSubmit hook — .claude/hooks/team_intercept.py
    2) scripts/feishu_router.py — 飞书群消息入口
"""
import json
import os
import re
import subprocess

_CMD_RE = re.compile(r"^/team\s*$")
_SCRIPTS_DIR = os.path.dirname(__file__)
_PROJECT_ROOT = os.path.join(_SCRIPTS_DIR, "..")


def parse(text: str):
    """匹配 /team, 返回 True 或 None。"""
    if not text:
        return None
    return True if _CMD_RE.match(text.strip()) else None


def _load_team():
    team_file = os.path.join(_PROJECT_ROOT, "team.json")
    try:
        with open(team_file) as f:
            return json.load(f)
    except Exception:
        return {"agents": {}}


def _tmux_session():
    return _load_team().get("session", "ClaudeTeam")


def _capture_last(session, agent, lines=3):
    try:
        r = subprocess.run(
            ["tmux", "capture-pane", "-t", f"{session}:{agent}", "-p",
             "-S", f"-{lines}"],
            capture_output=True, text=True, timeout=5)
        return r.stdout.rstrip() if r.returncode == 0 else None
    except Exception:
        return None


def collect_team_status(session=None):
    """采集每个 agent 的状态, 返回 list[dict]。"""
    if session is None:
        session = _tmux_session()
    team = _load_team()
    agents = team.get("agents", {})

    # 延迟导入避免循环依赖
    import sys
    sys.path.insert(0, _SCRIPTS_DIR)
    from concurrent.futures import ThreadPoolExecutor
    from cli_adapters import adapter_for_agent
    from config import resolve_model_for_agent, resolve_thinking_for_agent
    from tmux_utils import is_agent_idle

    items = list(agents.items())

    def probe(item):
        name, info = item
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
        pane = _capture_last(session, name)
        if pane is None:
            status = "offline"
        elif "\U0001f4a4 待 wake" in pane or "\U0001f4a4" in pane.split("\n")[-1]:
            status = "suspended \U0001f4a4"
        elif not is_agent_idle(session, name, adapter.busy_markers()):
            status = "busy"
        else:
            status = "idle"
        return {
            "name": name, "role": role, "cli": cli,
            "model": model, "thinking": thinking, "status": status,
        }

    # 并行 is_agent_idle：N agent × 3s 串行 → 整体 ~3s。
    with ThreadPoolExecutor(max_workers=max(1, len(items))) as ex:
        rows = list(ex.map(probe, items))
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
