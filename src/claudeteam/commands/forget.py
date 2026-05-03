"""`claudeteam forget <agent> [--yes]` — wipe an agent's durable memory.

Symmetric to `claudeteam recall`. Used when an agent's memory has
gotten poisoned (wrong learning recorded, stale task assignments
piling up) and starting fresh is cheaper than triaging which entries
are still useful.

Refuses to nuke without `--yes` so an operator typo doesn't take out
hours of accumulated context. The reset command (`claudeteam reset`)
already wipes the whole state dir; this is the per-agent scalpel.
"""
from __future__ import annotations

from claudeteam.store import memory
from claudeteam.util import (
    error_exit, maybe_print_help, pop_bool_flag, usage_error,
)


USAGE = "usage: claudeteam forget <agent> [--yes]"


def main(argv: list[str]) -> int:
    rest = list(argv)
    if maybe_print_help(rest, USAGE):
        return 0
    yes = pop_bool_flag(rest, "--yes")
    if len(rest) < 1:
        return usage_error(USAGE)
    agent = rest[0]
    if not yes:
        return error_exit(
            f"❌ refusing to wipe {agent}'s memory without --yes; "
            f"run `claudeteam recall {agent}` first to verify what "
            f"you're about to drop, then `claudeteam forget {agent} --yes`")
    n = memory.clear(agent)
    if n == 0:
        print(f"🧠 {agent}: nothing to forget (memory was already empty)")
    else:
        print(f"🗑  {agent}: forgot {n} memory entr"
              f"{'ies' if n != 1 else 'y'}")
    return 0
