"""共享 /tmux 解析 + 本地 tmux capture。

两处入口共用（都不能走 LLM）：
    1) Claude Code UserPromptSubmit hook — 覆盖 IDE / CLI 输入
    2) scripts/feishu_router.py — 覆盖飞书群消息入口

保持纯标准库，方便两处直接 import 或独立运行。
"""
import re
import subprocess

DEFAULT_AGENT = "manager"
DEFAULT_LINES = 10
MAX_LINES = 2000

_CMD_RE = re.compile(r"^/tmux(?:\s+([A-Za-z0-9_-]+))?(?:\s+(\d+))?\s*$")


def parse(text: str):
    """匹配 /tmux [agent] [lines], 返回 (agent, lines) 或 None。"""
    if not text:
        return None
    m = _CMD_RE.match(text.strip())
    if not m:
        return None
    agent = m.group(1) or DEFAULT_AGENT
    try:
        lines = int(m.group(2)) if m.group(2) else DEFAULT_LINES
    except ValueError:
        lines = DEFAULT_LINES
    return agent, max(1, min(lines, MAX_LINES))


def capture(session: str, agent: str, lines: int) -> str:
    """跑 tmux capture-pane 并格式化返回文本。失败给可读错误。"""
    target = f"{session}:{agent}"
    try:
        r = subprocess.run(
            ["tmux", "capture-pane", "-t", target, "-p", "-S", f"-{lines}"],
            capture_output=True, text=True, timeout=5)
    except Exception as e:
        return f"\u26a0\ufe0f tmux 调用失败: {e}"
    if r.returncode != 0:
        err = (r.stderr or "").strip() or "unknown error"
        return f"\u26a0\ufe0f 读取 tmux 窗口 `{target}` 失败: {err}"
    body = r.stdout.rstrip() or "(窗口为空)"
    return f"=== {target} 最后 {lines} 行 ===\n{body}"
