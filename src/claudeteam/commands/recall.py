"""`claudeteam recall <agent> [--limit N] [--kind K] [--json]`

Print an agent's durable memory entries. Symmetric to `claudeteam remember`.

Use cases:
  - operator audit: `claudeteam recall manager` to see what manager has
    been remembering across /clear cycles.
  - manager 巡视 a worker: `claudeteam recall worker_cc` from manager's
    pane to check what the worker has stored without going into worker_cc's
    tmux window.
  - debugging "agent forgot the task" — verify whether the memory entry
    was actually written.
  - kind filter: `claudeteam recall manager --kind decision` to scan
    one slice (round-107). Cross-checks against `memory.KNOWN_KINDS`
    so a typo (`--kind decsion`) doesn't silently return [] — prints
    a hint with the closest known kind.

Default output is human-readable bullets; `--json` dumps the underlying
records for piping to jq / smoke conductors.
"""
from __future__ import annotations

from claudeteam.store import memory
from claudeteam.util import (
    error_exit, fmt_time_ms, maybe_print_help, pop_bool_flag, pop_flag,
    print_json, usage_error, warn,
)


USAGE = (
    "usage: claudeteam recall <agent> [--limit N] [--kind K] [--json]\n"
    f"       known kinds: {memory.kinds_summary()}\n"
    "       (--kind accepts any string; unknown kinds get a stderr nudge)"
)

_DEFAULT_LIMIT = 20

# When a kind filter doesn't match any KNOWN_KINDS entry, fetch enough
# rows that the filter has a real chance of finding something even if
# the agent has lots of unrelated entries (200 = full memory cap).
_FILTERED_FETCH = memory._MAX_PER_AGENT


def main(argv: list[str]) -> int:
    rest = list(argv)
    if maybe_print_help(rest, USAGE):
        return 0
    as_json = pop_bool_flag(rest, "--json")
    raw_limit = pop_flag(rest, "--limit")
    kind_filter = pop_flag(rest, "--kind") or ""
    try:
        limit = int(raw_limit) if raw_limit else _DEFAULT_LIMIT
    except ValueError:
        return error_exit(f"❌ --limit must be an integer (got {raw_limit!r})")
    if limit < 1:
        return error_exit("❌ --limit must be >= 1")
    if len(rest) < 1:
        return usage_error(USAGE)
    agent = rest[0]

    if kind_filter and kind_filter not in memory.KNOWN_KINDS:
        # Soft warn — proceed but tell the operator the filter is
        # unconventional. 0 results may still be the right answer
        # (the agent might have written a `fyi`-kind entry). Hint at
        # the convention so a typo of a real kind is obvious.
        warn(f"⚠️  --kind {kind_filter!r} not in known kinds "
             f"({sorted(memory.KNOWN_KINDS)}); proceeding anyway")

    if kind_filter:
        # Pull the full memory window so the kind filter has the entire
        # backlog to scan; then trim to `limit` AFTER filtering, so the
        # operator sees `limit` matches not `limit` total reads.
        all_rows = memory.list_recent(agent, limit=_FILTERED_FETCH)
        rows = [r for r in all_rows if r.get("kind") == kind_filter][-limit:]
    else:
        rows = memory.list_recent(agent, limit=limit)

    if as_json:
        print_json(rows)
        return 0

    if not rows:
        suffix = f" (kind={kind_filter})" if kind_filter else ""
        print(f"🧠 {agent}: no memory entries{suffix}")
        return 0
    filter_note = f", filter kind={kind_filter}" if kind_filter else ""
    print(f"🧠 {agent}: {len(rows)} entr{'ies' if len(rows) != 1 else 'y'} "
          f"(oldest first, capped at {limit}{filter_note})")
    for row in rows:
        ts = fmt_time_ms(row.get("created_at", 0))
        kind = row.get("kind", "?")
        content = row.get("content", "")
        ref = row.get("ref", "")
        suffix = f"  (ref={ref})" if ref else ""
        print(f"  [{ts}] [{kind}] {content}{suffix}")
    return 0
