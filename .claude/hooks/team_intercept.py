#!/usr/bin/env python3
"""UserPromptSubmit hook: 拦截 /team, 零 LLM 调用, 输出团队状态表。

机制同 tmux_intercept.py:
    匹配 /team → block(团队状态表)
    不匹配 → exit 0 放行
"""
import json
import os
import sys
from pathlib import Path

PROJECT_ROOT = Path(os.environ.get("CLAUDE_PROJECT_DIR") or
                    Path(__file__).resolve().parents[2])

sys.path.insert(0, str(PROJECT_ROOT / "src"))
from claudeteam.commands import team as team_command


def block(reason: str) -> None:
    print(json.dumps({"decision": "block", "reason": reason},
          ensure_ascii=False))
    sys.exit(0)


def main() -> None:
    try:
        payload = json.load(sys.stdin)
    except Exception:
        sys.exit(0)

    prompt = (payload.get("prompt") or "").strip()
    if not team_command.parse(prompt):
        sys.exit(0)

    session = team_command._tmux_session()
    block(team_command.collect_and_format(session))


if __name__ == "__main__":
    main()
