#!/usr/bin/env python3
"""UserPromptSubmit hook: 拦截 /health，输出主机 + 容器 + 员工负载快照。

复用 scripts/slash_commands.py 的采集逻辑；hook 只取 text 部分
（decision:block 不支持卡片）。
"""
import json
import os
import re
import sys
from pathlib import Path

PROJECT_ROOT = Path(os.environ.get("CLAUDE_PROJECT_DIR") or
                    Path(__file__).resolve().parents[2])
sys.path.insert(0, str(PROJECT_ROOT / "scripts"))

import slash_commands  # noqa: E402


def block(reason: str) -> None:
    print(json.dumps({"decision": "block", "reason": reason}, ensure_ascii=False))
    sys.exit(0)


def main() -> None:
    try:
        payload = json.load(sys.stdin)
    except Exception:
        sys.exit(0)
    prompt = (payload.get("prompt") or "").strip()
    if not re.fullmatch(r"/health\s*", prompt):
        sys.exit(0)

    try:
        matched, reply = slash_commands.dispatch(prompt)
    except Exception as e:
        block(f"⚠️ /health 执行异常：{type(e).__name__}: {e}")
    if not matched:
        sys.exit(0)
    if isinstance(reply, dict):
        body = reply.get("text") or ""
    else:
        body = str(reply) if reply else ""
    block(body or "(无输出)")


if __name__ == "__main__":
    main()
