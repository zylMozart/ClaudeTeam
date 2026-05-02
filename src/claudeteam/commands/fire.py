"""`claudeteam fire <agent>`

Kill an agent's tmux window and mark its status.  Refuses to fire 'manager'
(too disruptive — kill the whole session if you want that).
"""
from __future__ import annotations

import sys

from claudeteam.runtime import config, tmux
from claudeteam.store import local_facts
from claudeteam.util import usage_error


USAGE = "usage: claudeteam fire <agent>"


def main(argv: list[str]) -> int:
    if len(argv) < 1:
        return usage_error(USAGE)
    agent = argv[0]

    if agent == "manager":
        print("❌ refusing to fire manager (kill the tmux session yourself if you mean it)",
              file=sys.stderr)
        return 1

    session = config.session_name()
    target = tmux.Target(session, agent)
    if not tmux.has_window(target):
        print(f"⚠️  {agent} has no pane in session {session}")
        local_facts.upsert_status(agent, "已停止", "fired (no pane)")
        return 0

    # send Ctrl-C to interrupt whatever's running, then kill the window
    tmux.send_keys(target, "C-c")
    tmux.kill_window(target)

    local_facts.upsert_status(agent, "已停止", "fired")
    print(f"✅ fired: {agent}")
    return 0
