"""`claudeteam team`

Show the latest status for every agent that has reported one.  Single-line
per agent: `name  status  task  [⛔ blocker]  (Nm ago)`.
"""
from __future__ import annotations

from claudeteam.store import local_facts
from claudeteam.util import ago_ms


def main(argv: list[str]) -> int:
    rows = local_facts.list_all_statuses()
    if not rows:
        print("👥 no agents have reported status yet")
        return 0
    heartbeats = local_facts.all_heartbeats()
    name_w = max(len(r["agent"]) for r in rows)
    for r in rows:
        line = (
            f"{r['agent'].ljust(name_w)}  "
            f"{r['status']}  {r['task']}"
        )
        if r.get("blocker"):
            line += f"  ⛔ {r['blocker']}"
        line += f"  ({ago_ms(r.get('updated_at', 0))})"
        hb = heartbeats.get(r["agent"])
        if hb:
            line += f"  ♥ {ago_ms(hb)}"
        print(line)
    return 0
