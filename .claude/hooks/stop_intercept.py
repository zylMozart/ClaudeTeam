#!/usr/bin/env python3
"""UserPromptSubmit hook: 拦截 /stop <agent>，复用 slash_commands.dispatch。

用法：
    /stop            → 回显用法（无副作用）
    /stop devops     → 给 devops 发 C-c 中断当前动作
"""
import json
import os
import re
import sys
from pathlib import Path

PROJECT_ROOT = Path(os.environ.get("CLAUDE_PROJECT_DIR") or
                    Path(__file__).resolve().parents[2])
sys.path.insert(0, str(PROJECT_ROOT / "src"))

from claudeteam.commands.slash.standalone import dispatch as _slash_dispatch  # noqa: E402


def block(reason: str) -> None:
    print(json.dumps({"decision": "block", "reason": reason}, ensure_ascii=False))
    sys.exit(0)


def main() -> None:
    try:
        payload = json.load(sys.stdin)
    except Exception:
        sys.exit(0)
    prompt = (payload.get("prompt") or "").strip()
    if not re.match(r"^/stop(?:\s|$)", prompt):
        sys.exit(0)

    try:
        matched, reply = _slash_dispatch(prompt)
    except Exception as e:
        block(f"⚠️ /stop 执行异常：{type(e).__name__}: {e}")
    if not matched:
        sys.exit(0)
    body = reply.get("text") if isinstance(reply, dict) else (str(reply) if reply else "")
    block(body or "(无输出)")


if __name__ == "__main__":
    main()
