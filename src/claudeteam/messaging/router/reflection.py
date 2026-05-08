"""Boss message counter and reflection meeting trigger.

Every boss message delivered to manager increments a persistent counter.
When it reaches the threshold (default 30), and all agents are idle,
a reflection meeting is triggered: each agent gets a reflection prompt,
and the manager collects and summarizes results for the boss.
"""
from __future__ import annotations

import json
import os
import time

REFLECTION_THRESHOLD = 30

_COUNTER_FILENAME = "boss_msg_counter.json"


def _counter_path(state_dir: str) -> str:
    return os.path.join(state_dir, _COUNTER_FILENAME)


def load_counter(state_dir: str) -> dict:
    path = _counter_path(state_dir)
    if not os.path.exists(path):
        return {"count": 0, "last_meeting_ts": 0}
    try:
        with open(path, encoding="utf-8") as f:
            data = json.load(f)
        return {"count": int(data.get("count", 0)), "last_meeting_ts": float(data.get("last_meeting_ts", 0))}
    except Exception:
        return {"count": 0, "last_meeting_ts": 0}


def save_counter(state_dir: str, data: dict) -> None:
    path = _counter_path(state_dir)
    os.makedirs(os.path.dirname(path), exist_ok=True)
    tmp = path + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(data, f)
    os.replace(tmp, path)


def increment_and_check(state_dir: str, threshold: int = REFLECTION_THRESHOLD) -> bool:
    """Increment boss message counter. Return True if threshold reached."""
    data = load_counter(state_dir)
    data["count"] = data["count"] + 1
    reached = data["count"] >= threshold
    save_counter(state_dir, data)
    return reached


def reset_counter(state_dir: str) -> None:
    data = load_counter(state_dir)
    data["count"] = 0
    data["last_meeting_ts"] = time.time()
    save_counter(state_dir, data)


def build_reflection_prompt(agent: str, msg_count: int) -> str:
    return (
        f"【反思大会】主管已经处理了 {msg_count} 条老板消息。\n"
        f"请你花 2-3 分钟回顾最近的工作，思考以下问题：\n"
        f"- 最近做得好的地方是什么？\n"
        f"- 有没有重复犯的错误或低效的流程？\n"
        f"- 有什么改进建议？\n\n"
        f"请用 python3 scripts/feishu_msg.py send manager {agent} '你的反思总结' 提交你的反思。"
    )
