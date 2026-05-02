"""Single console-scripts entry point for the `claudeteam` command.

Subcommands are registered in COMMANDS as `name → handler(argv)` pairs.  Each
handler returns an int exit code or None (treated as 0).  This module owns
the top-level dispatch, usage text, and process exit; subcommand modules
own their own argv parsing and side effects.
"""
from __future__ import annotations

import sys
from typing import Callable


CommandHandler = Callable[[list[str]], int | None]


# Subcommand registry. Adding a new command means: write a module under
# claudeteam.commands, expose a `main(argv)` callable, register it here.
from claudeteam.commands import (
    init, send, inbox, read, status, log, team, workspace,
    start, hire, fire, up, down, reset,
    say, router, watchdog, task,
    health, usage, install_hooks,
)

COMMANDS: dict[str, CommandHandler] = {
    # bootstrap
    "init": init.main,
    # local store I/O
    "send": send.main,
    "inbox": inbox.main,
    "read": read.main,
    "status": status.main,
    "log": log.main,
    "team": team.main,
    "workspace": workspace.main,
    # team lifecycle
    "start": start.main,
    "hire": hire.main,
    "fire": fire.main,
    "up": up.main,
    "down": down.main,
    "reset": reset.main,
    # feishu transport
    "say": say.main,
    "router": router.main,
    # supervision
    "watchdog": watchdog.main,
    # task tracking
    "task": task.main,
    # operational
    "health": health.main,
    "usage": usage.main,
    "install-hooks": install_hooks.main,
}


def _usage() -> str:
    lines = [
        "usage: claudeteam <command> [args...]",
        "",
        "commands:",
    ]
    for name in sorted(COMMANDS):
        lines.append(f"  {name}")
    if not COMMANDS:
        lines.append("  (none registered yet)")
    return "\n".join(lines)


def main(argv: list[str] | None = None) -> int:
    args = list(argv if argv is not None else sys.argv[1:])
    if not args or args[0] in ("-h", "--help", "help"):
        print(_usage())
        return 0
    cmd, rest = args[0], args[1:]
    handler = COMMANDS.get(cmd)
    if handler is None:
        print(f"unknown command: {cmd}\n", file=sys.stderr)
        print(_usage(), file=sys.stderr)
        return 1
    return int(handler(rest) or 0)


if __name__ == "__main__":
    raise SystemExit(main())
