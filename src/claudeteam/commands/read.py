"""`claudeteam read <local_id>`

Mark a message as read by its local id.  Returns 1 if no such message.
"""
from __future__ import annotations

from claudeteam.store import local_facts
from claudeteam.util import error_exit, usage_error


USAGE = "usage: claudeteam read <local_id>"


def main(argv: list[str]) -> int:
    if len(argv) < 1:
        return usage_error(USAGE)
    local_id = argv[0]
    if not local_facts.mark_read(local_id):
        return error_exit(f"❌ no such message: {local_id}")
    print(f"✅ marked read: {local_id}")
    return 0
