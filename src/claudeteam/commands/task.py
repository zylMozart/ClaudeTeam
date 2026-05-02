"""`claudeteam task <subcommand>`

  task create <assignee> <title> [--by <agent>] [--desc <text>]
  task update <id>       [--status S] [--assignee A] [--title T] [--desc D]
  task list              [--status S] [--assignee A]
  task get <id>
  task done <id>          (alias for `update <id> --status 已完成`)
"""
from __future__ import annotations

import sys

from claudeteam.store import tasks
from claudeteam.util import fmt_time_ms, pop_flag, usage_error


USAGE = (
    "usage:\n"
    "  claudeteam task create <assignee> <title> [--by <agent>] [--desc <text>]\n"
    "  claudeteam task update <id>  [--status S] [--assignee A] [--title T] [--desc D]\n"
    "  claudeteam task list  [--status S] [--assignee A]\n"
    "  claudeteam task get <id>\n"
    "  claudeteam task done <id>"
)


def _fmt_task(t: dict) -> list[str]:
    ts = fmt_time_ms(t["created_at"])
    head = f"{t['id']}  [{t['status']}]  {t['title']}"
    body = [f"  assignee: {t.get('assignee') or '-'}"]
    if t.get("creator"):
        body.append(f"  by: {t['creator']}")
    if t.get("description"):
        body.append(f"  desc: {t['description']}")
    body.append(f"  created: {ts}")
    return [head] + body


def _cmd_create(rest: list[str]) -> int:
    by = pop_flag(rest, "--by") or ""
    desc = pop_flag(rest, "--desc") or ""
    if len(rest) < 2:
        return usage_error(USAGE)
    assignee = rest[0]
    title = " ".join(rest[1:])
    try:
        tid = tasks.create(assignee, title, description=desc, creator=by)
    except ValueError as e:
        print(f"❌ {e}", file=sys.stderr)
        return 1
    print(f"✅ created {tid}: {title} → {assignee}")
    return 0


def _cmd_update(rest: list[str]) -> int:
    status = pop_flag(rest, "--status")
    assignee = pop_flag(rest, "--assignee")
    title = pop_flag(rest, "--title")
    desc = pop_flag(rest, "--desc")
    if len(rest) < 1:
        return usage_error(USAGE)
    tid = rest[0]
    try:
        ok = tasks.update(tid, status=status, assignee=assignee,
                          title=title, description=desc)
    except ValueError as e:
        print(f"❌ {e}", file=sys.stderr)
        return 1
    if not ok:
        print(f"❌ no such task: {tid}", file=sys.stderr)
        return 1
    print(f"✅ updated {tid}")
    return 0


def _cmd_done(rest: list[str]) -> int:
    if len(rest) < 1:
        return usage_error(USAGE)
    return _cmd_update([rest[0], "--status", "已完成"])


def _cmd_list(rest: list[str]) -> int:
    status = pop_flag(rest, "--status")
    assignee = pop_flag(rest, "--assignee")
    rows = tasks.list_tasks(status=status, assignee=assignee)
    if not rows:
        print("📋 no matching tasks")
        return 0
    print(f"📋 {len(rows)} tasks")
    for t in rows:
        for line in _fmt_task(t):
            print(line)
        print()
    return 0


def _cmd_get(rest: list[str]) -> int:
    if len(rest) < 1:
        return usage_error(USAGE)
    t = tasks.get(rest[0])
    if t is None:
        print(f"❌ no such task: {rest[0]}", file=sys.stderr)
        return 1
    for line in _fmt_task(t):
        print(line)
    return 0


SUBCOMMANDS = {
    "create": _cmd_create,
    "update": _cmd_update,
    "done":   _cmd_done,
    "list":   _cmd_list,
    "get":    _cmd_get,
}


def main(argv: list[str]) -> int:
    if not argv or argv[0] in ("-h", "--help"):
        print(USAGE)
        return 0 if argv else 1
    sub = argv[0]
    if sub not in SUBCOMMANDS:
        print(f"unknown task subcommand: {sub}\n{USAGE}", file=sys.stderr)
        return 1
    return SUBCOMMANDS[sub](list(argv[1:]))
