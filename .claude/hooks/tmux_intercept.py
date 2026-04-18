#!/usr/bin/env python3
"""UserPromptSubmit hook: 拦截 /tmux [agent] [lines], 本地 tmux capture-pane, 不
触发 LLM。

用法:
    /tmux                → manager 最后 10 行
    /tmux devops         → devops 最后 10 行
    /tmux devops 30      → devops 最后 30 行

机制:
    Claude Code UserPromptSubmit hook。匹配到 /tmux 前缀则输出
        {"decision":"block","reason":....}, prompt 不会被送到模型，
        reason 直接显示给用户; 不匹配则 exit 0 放行。
"""
import json
import os
import re
import subprocess
import sys
from pathlib import Path

PROJECT_ROOT = Path(os.environ.get("CLAUDE_PROJECT_DIR") or
                    Path(__file__).resolve().parents[2])

DEFAULT_AGENT = "manager"
DEFAULT_LINES = 10
MAX_LINES = 2000


def load_session() -> str:
    team_json = PROJECT_ROOT / "team.json"
    try:
        return json.loads(team_json.read_text()).get("session", "ClaudeTeam")
    except Exception:
        return "ClaudeTeam"


def capture(target: str, lines: int) -> str:
    r = subprocess.run(
        ["tmux", "capture-pane", "-t", target, "-p", "-S", f"-{lines}"],
        capture_output=True, text=True, timeout=5)
    if r.returncode != 0:
        err = (r.stderr or "").strip() or "unknown error"
        return f"\u26a0\ufe0f 读取 tmux 窗口 `{target}` 失败: {err}"
    body = r.stdout.rstrip() or "(窗口为空)"
    return f"=== {target} 最后 {lines} 行 ===\n{body}"


def block(reason: str) -> None:
    print(json.dumps({"decision": "block", "reason": reason},
          ensure_ascii=False))
    sys.exit(0)


def main() -> None:
    try:
        payload = json.load(sys.stdin)
    except Exception:
        sys.exit(0)

    prompt = (payload.get("prompt") or "").strip()
    m = re.match(r"^/tmux(?:\s+(\S+))?(?:\s+(\d+))?\s*$", prompt)
    if not m:
        sys.exit(0)  # 非 /tmux，放行交给模型

    agent = m.group(1) or DEFAULT_AGENT
    try:
        lines = int(m.group(2)) if m.group(2) else DEFAULT_LINES
    except ValueError:
        lines = DEFAULT_LINES
    lines = max(1, min(lines, MAX_LINES))

    if not re.fullmatch(r"[A-Za-z0-9_-]+", agent):
        block(f"\u26a0\ufe0f 非法 agent 名: `{agent}`")

    session = load_session()
    block(capture(f"{session}:{agent}", lines))


if __name__ == "__main__":
    main()
