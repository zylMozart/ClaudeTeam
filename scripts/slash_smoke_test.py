#!/usr/bin/env python3
"""模拟老板在群里发 /xxx 指令,直接 inject fake event 到 router.handle_event。

效果与老板从飞书输入一致:
  - 走 feishu_router.handle_event() 全部管线
  - dispatch 命中 → cmd_say(...) 回显群聊
  - 不路由到 manager,manager 会话无感

仅供冒烟测试。每条命令间 sleep 2s 避免 lark API 限速。
"""
import os
import sys
import time
import uuid

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import feishu_router

CHAT_ID = "oc_1b717b31bdde9f0764b3beaea2fc6c05"
BOSS_OPEN_ID = "ou_e611594177c535dc77f984f050d37638"

CASES = [
    "/help",
    "/team",          # 现在应走卡片
    "/usage",         # 现在应走卡片
    "/health",        # 走卡片:主机+容器+员工
    "/tmux",
    "/tmux devops 5",
    "/send",          # 不带参,走用法提示
    "/stop",          # 无参走用法提示(带参会真 C-c,留给老板手动试)
    # /compact-all 已删除
    # /compact 无参 — 故意放行给 Claude 原生
    # /send <agent> <msg> — 会真的注入到员工,留给老板手动试
    # /clear <agent> — 会真的 rehire,留给老板手动试
]


def fake_event(text: str) -> dict:
    return {
        "message_id": f"smoke_{uuid.uuid4().hex[:12]}",
        "chat_id": CHAT_ID,
        "sender_id": BOSS_OPEN_ID,
        "text": text,
        "message_type": "text",
    }


def main():
    print(f"🧪 冒烟测试开始 — {len(CASES)} 条命令\n")
    for i, cmd in enumerate(CASES, 1):
        print(f"[{i}/{len(CASES)}] 注入: {cmd!r}")
        ev = fake_event(cmd)
        try:
            feishu_router.handle_event(ev)
            print(f"         ✅ handle_event 返回")
        except Exception as e:
            print(f"         ❌ 异常: {e}")
        time.sleep(2)
    print("\n✅ 冒烟测试结束,看群里有无 7 条 bot 回显(扣掉 /compact 无参那条跳过)")


if __name__ == "__main__":
    main()
