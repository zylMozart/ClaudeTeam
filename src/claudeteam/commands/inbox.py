"""`claudeteam inbox <agent>`

List unread messages for an agent. Read messages don't appear by default.
"""
from __future__ import annotations

import sys

from claudeteam.store import local_facts
from claudeteam.util import fmt_time_ms, usage_error


USAGE = "usage: claudeteam inbox <agent>"


def main(argv: list[str]) -> int:
    if len(argv) < 1:
        return usage_error(USAGE)
    agent = argv[0]
    local_facts.touch_heartbeat(agent)
    msgs = local_facts.list_messages(agent, unread_only=True)
    if not msgs:
        print(f"📭 {agent}: no unread messages")
        return 0
    print(f"📬 {agent}: {len(msgs)} unread")
    for m in msgs:
        ts = fmt_time_ms(m.get("created_at", 0))
        local_id = m.get("local_id", "")
        frm = m.get("from", "?")
        priority = m.get("priority", "?")
        content = m.get("content", "")
        print(f"── [{ts}] {frm} → {agent}  [{priority}]  {local_id}")
        print(f"   {content}")
    return 0
