#!/usr/bin/env python3
"""UserPromptSubmit hook: 拦截 /help，委托 slash dispatch 返回帮助文本。"""
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


def main():
    try:
        payload = json.load(sys.stdin)
    except Exception:
        sys.exit(0)
    prompt = (payload.get("prompt") or "").strip()
    if not re.fullmatch(r"/help\s*", prompt):
        sys.exit(0)
    try:
        matched, reply = _slash_dispatch(prompt)
    except Exception as e:
        block(f"⚠️ /help 执行异常：{type(e).__name__}: {e}")
    if not matched:
        sys.exit(0)
    text = reply if isinstance(reply, str) else reply.get("text", str(reply))
    block(text)


if __name__ == "__main__":
    main()
