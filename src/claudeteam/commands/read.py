"""`claudeteam read <local_id>`

Mark a message as read by its local id.  Returns 1 if no such message.
"""
from __future__ import annotations

import sys

from claudeteam.store import local_facts
from claudeteam.util import usage_error


USAGE = "usage: claudeteam read <local_id>"


def main(argv: list[str]) -> int:
    if len(argv) < 1:
        return usage_error(USAGE)
    local_id = argv[0]
    if not local_facts.mark_read(local_id):
        print(f"❌ no such message: {local_id}", file=sys.stderr)
        return 1
    print(f"✅ marked read: {local_id}")
    return 0
