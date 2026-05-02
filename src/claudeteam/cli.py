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
    init as _init,
    send as _send,
    inbox as _inbox,
    read as _read,
    status as _status,
    log as _log,
    team as _team,
    workspace as _workspace,
    start as _start,
    hire as _hire,
    fire as _fire,
    say as _say,
    router as _router,
    watchdog as _watchdog,
    task as _task,
    health as _health,
    up as _up,
    down as _down,
    usage as _usage_cmd,
)

COMMANDS: dict[str, CommandHandler] = {
    # bootstrap
    "init": _init.main,
    # local store I/O
    "send": _send.main,
    "inbox": _inbox.main,
    "read": _read.main,
    "status": _status.main,
    "log": _log.main,
    "team": _team.main,
    "workspace": _workspace.main,
    # team lifecycle
    "start": _start.main,
    "hire": _hire.main,
    "fire": _fire.main,
    "up": _up.main,
    "down": _down.main,
    # feishu transport
    "say": _say.main,
    "router": _router.main,
    # supervision
    "watchdog": _watchdog.main,
    # task tracking
    "task": _task.main,
    # operational
    "health": _health.main,
    "usage": _usage_cmd.main,
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
