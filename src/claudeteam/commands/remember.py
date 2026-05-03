"""`claudeteam remember <agent> <kind> <content> [--ref <ref>]`

Append an entry to `<agent>`'s durable memory (round-83 store/memory.py).
Memory survives tmux pane restart / `/clear`, gets injected into the
agent's identity init prompt on wake (round-84) so context carries over.

Example:
    claudeteam remember manager task_assigned "round-87 implement remember cmd" --ref om_xx
    claudeteam remember worker_cc learning "auth uses bcrypt; salt rounds=12"
    claudeteam remember worker_codex blocker "blocked on missing GH PAT" --ref T-42

Convention for `kind` (not enforced):
    task_assigned / task_completed / learning / blocker / decision / note
"""
from __future__ import annotations

from claudeteam.store import memory
from claudeteam.util import maybe_print_help, pop_flag, usage_error


USAGE = (
    "usage: claudeteam remember <agent> <kind> <content> [--ref <ref>]\n"
    f"       known kinds: {memory.kinds_summary()}\n"
    "       (any string accepted; unknown kinds get a stderr nudge)"
)


def main(argv: list[str]) -> int:
    rest = list(argv)
    if maybe_print_help(rest, USAGE):
        return 0
    ref = pop_flag(rest, "--ref") or ""
    if len(rest) < 3:
        return usage_error(USAGE)
    agent = rest[0]
    kind = rest[1]
    # Join everything after kind into a single content string (so callers
    # can pass an unquoted message without surprising arg-count errors).
    content = " ".join(rest[2:])
    record = memory.append(agent, kind, content, ref=ref)
    suffix = f" (ref={ref})" if ref else ""
    print(f"🧠 remembered: {agent}/{kind}{suffix}  [{record['created_at']}]")
    return 0
