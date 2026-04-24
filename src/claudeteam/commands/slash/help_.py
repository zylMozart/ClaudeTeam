"""Handler for /help slash command."""
from __future__ import annotations

import re

HELP_TEXT = """🆘 ClaudeTeam 自定义斜杠命令（零 LLM，router/hook 直拦）

/help                    → 本帮助
/team                    → 所有员工实时 tmux 状态（卡片）
/usage                   → Claude Max 周额度 + Extra usage 快照（卡片）
/health                  → 主机 + 容器 + 员工资源占用（卡片）
/tmux [agent] [lines]    → capture-pane 窗口（默认 manager/10 行）
/send <agent> <msg>      → 直接注入消息到 agent 窗口
/compact [agent]         → 群聊无参=压缩 manager；带参压缩指定 agent
/stop <agent>            → 送 C-c 到 agent 窗口（中断当前动作）
/clear <agent>           → 送 /clear + 重新入职 init_msg（相当于 rehire）
"""


def handle(text: str, ctx=None) -> str | None:
    """Return help text for /help, else None."""
    return HELP_TEXT if re.fullmatch(r"/help\s*", text) else None
