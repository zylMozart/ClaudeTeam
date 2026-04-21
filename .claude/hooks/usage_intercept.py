#!/usr/bin/env python3
"""UserPromptSubmit hook: 拦截 /usage，直接跑 scripts/usage_snapshot.py 回显，不触发 LLM。

用法：
    /usage       → 直接打印 Claude Max 额度快照

机制：
    匹配 /usage 前缀则 decision=block + reason=脚本输出；不匹配 exit 0 放行。
"""
import json
import os
import re
import subprocess
import sys
from pathlib import Path

PROJECT_ROOT = Path(os.environ.get("CLAUDE_PROJECT_DIR") or
                    Path(__file__).resolve().parents[2])
SNAPSHOT = PROJECT_ROOT / "scripts" / "usage_snapshot.py"


def block(reason: str) -> None:
    print(json.dumps({"decision": "block", "reason": reason}, ensure_ascii=False))
    sys.exit(0)


def main() -> None:
    try:
        payload = json.load(sys.stdin)
    except Exception:
        sys.exit(0)

    prompt = (payload.get("prompt") or "").strip()
    if not re.fullmatch(r"/usage\s*", prompt):
        sys.exit(0)

    try:
        r = subprocess.run(
            ["python3", str(SNAPSHOT)],
            capture_output=True, text=True, timeout=30)
    except subprocess.TimeoutExpired:
        block("⚠️ /usage: 调 api.anthropic.com 超时 30s")
    except Exception as e:
        block(f"⚠️ /usage 执行出错: {e}")

    body = (r.stdout or "").rstrip()
    err = (r.stderr or "").rstrip()
    if r.returncode != 0:
        block(f"⚠️ /usage 失败 (exit {r.returncode})\n{err or body or '(无输出)'}")
    block(body or err or "(无输出)")


if __name__ == "__main__":
    main()
