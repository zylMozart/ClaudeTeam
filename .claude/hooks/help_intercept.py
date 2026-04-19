#!/usr/bin/env python3
"""UserPromptSubmit hook: 拦截 /help，列出所有自定义斜杠命令，不触发 LLM。"""
import json
import re
import sys

HELP_TEXT = """🆘 **ClaudeTeam 自定义斜杠命令**（零 LLM，hook 直拦）

**/help**                    → 显示本帮助
**/team**                    → 列所有员工实时 tmux 状态（群聊为卡片）
**/usage**                   → Claude Max 周额度 + Extra usage 快照（群聊为卡片）
**/server-load**             → 主机 + 容器 + 员工资源占用快照（群聊为卡片）
**/tmux [agent] [lines]**    → capture-pane 某 agent 的 tmux 窗口
                              例：`/tmux devops 30` → devops 最后 30 行
                              默认 manager / 10 行
**/send <agent> <message>**  → 直接往某 agent 的 tmux 窗口塞消息（绕开飞书 router）
                              例：`/send devops 马上停`
                              本机 + 所有 claudeteam-* 容器一起扫，找到就注入
**/compact [agent]**         → 给单个 agent 发 /compact
                              无参 → 走 Claude Code 内置 /compact（压缩自己 = manager）
                              带参 → `/compact devops` 只压缩 devops
**/stop <agent>**            → 给 agent 发 Ctrl+C 中断当前动作（白名单，本机+容器一起扫）
                              例：`/stop devops`
**/clear <agent>**           → 给 agent 送 /clear 清上下文 + 自动重新入职 init_msg
                              例：`/clear devops`（相当于远程 rehire，⚠️ 会丢会话记忆）
                              无参 → 走 Claude Code 内置 /clear（清自己）

**内置命令**（Claude Code 自带，hook 放行不拦）：
/clear(无参) /compact(无参) /model /permissions /mcp /status /loop /memory 等

**新增命令方法**：抄 `.claude/hooks/usage_intercept.py`，改正则+逻辑，settings.json hooks 数组里 append 一条。
"""


def main():
    try:
        payload = json.load(sys.stdin)
    except Exception:
        sys.exit(0)
    prompt = (payload.get("prompt") or "").strip()
    if not re.fullmatch(r"/help\s*", prompt):
        sys.exit(0)
    print(json.dumps({"decision": "block", "reason": HELP_TEXT}, ensure_ascii=False))


if __name__ == "__main__":
    main()
