"""`claudeteam reset` — tear down the team and wipe its runtime state.

Deletes everything under `$CLAUDETEAM_STATE_DIR`: facts/inbox, status,
logs, heartbeats, tasks, the router cursor, agent identity files, pid
files. Does NOT touch `team.json` or `runtime_config.json` — those are
configuration, not state.

Refuses to run unless `--yes` is passed (or stdin is a TTY and the
operator confirms interactively); state loss is intentional and the
operator should know they're about to do it.

Order:
  1. Best-effort `down` — kill watchdog + router, kill tmux session
  2. rmtree state_dir
"""
from __future__ import annotations

import shutil
import sys

from claudeteam.commands import down as _down
from claudeteam.runtime import paths


USAGE = "usage: claudeteam reset [--yes]"


def _confirm() -> bool:
    if not sys.stdin.isatty():
        return False
    try:
        ans = input("⚠️  delete all runtime state under $CLAUDETEAM_STATE_DIR? [y/N] ")
    except (EOFError, KeyboardInterrupt):
        return False
    return ans.strip().lower() in ("y", "yes")


def main(argv: list[str]) -> int:
    rest = list(argv)
    if "-h" in rest or "--help" in rest:
        print(USAGE)
        return 0
    yes = "--yes" in rest
    if yes:
        rest.remove("--yes")
    if rest:
        print(f"❌ unexpected args: {rest}\n{USAGE}", file=sys.stderr)
        return 1

    sd = paths.state_dir()
    if not yes and not _confirm():
        print("aborted", file=sys.stderr)
        return 1

    print("→ stopping daemons + tmux session")
    _down.main([])

    if sd.exists():
        shutil.rmtree(sd)
        print(f"🗑  wiped {sd}")
    else:
        print(f"⏭  {sd} did not exist")

    print("✅ reset complete (config files preserved)")
    return 0
