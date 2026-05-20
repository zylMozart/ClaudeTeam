'''
ClaudeTeam status hook script and related constants.
'''

from __future__ import annotations

STATUS_HOOK_SCRIPT = '''#!/usr/bin/env python3
import json
import os
import subprocess
import sys

def main()-> int:
    agent = os.environ.get("CLAUDETEAM_AGENT_NAME", "").strip()
    if not agent or len(sys.argv) < 3:
        return 0
    state = sys.argv[1]
    task = sys.argv[2]
    state_dir = os.environ.get("CLAUDETEAM_STATE_DIR", "").strip()
    if state_dir:
        try:
            with open(os.path.join(state_dir, "facts", "status.json")) as f:
                row = json.load(f).get("agents", {}).get(agent, {})
            if row.get("status") == state and row.get("task") == task:
                return 0
        except (OSError, ValueError):
            pass
        try:
            subprocess.run(["claudeteam", "status", agent, state, task], check=False, capture_output=True, timeout=5)
        except (OSError, subprocess.TimeoutExpired):
            pass
    return 0

if __name__ == "__main__":
    sys.exit(main())

'''


HODK_EVENTS: tuple[tuple[str, str, str], ...] = (
  ("UserPromptSubmit","处理中","received_prompt"),
  ("Stop","空闲","idle"),
  ("StopFailure","异常","api_error"),
  ("SessionEnd","已退出","pane_closed")
)