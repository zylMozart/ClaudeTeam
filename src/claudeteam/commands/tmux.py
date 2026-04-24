"""共享 /tmux 解析 + 本地 tmux capture。

两处入口共用（都不能走 LLM）：
    1) Claude Code UserPromptSubmit hook — 覆盖 IDE / CLI 输入
    2) scripts/feishu_router.py — 覆盖飞书群消息入口

保持纯标准库，方便两处直接 import 或独立运行。
"""
import re

from claudeteam.runtime.tmux_utils import capture_pane as _capture_pane

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
    """调用 tmux_utils.capture_pane 并格式化返回文本。失败给可读错误。"""
    target = f"{session}:{agent}"
    content = _capture_pane(session, agent, lines=lines)
    if content == "" and not _window_exists(session, agent):
        return f"\u26a0\ufe0f 读取 tmux 窗口 `{target}` 失败: 窗口不存在"
    body = content.rstrip() or "(窗口为空)"
    return f"=== {target} 最后 {lines} 行 ===\n{body}"


def _window_exists(session: str, agent: str) -> bool:
    import subprocess
    r = subprocess.run(["tmux", "has-session", "-t", f"{session}:{agent}"],
                       capture_output=True, timeout=5)
    return r.returncode == 0
