"""`claudeteam team [--json]`

Show the latest status for every agent that has reported one. Default:
human-readable single-line per agent:
  `name  status  task  [⛔ blocker]  (Nm ago)  ♥ Nm ago`.

With `--json`, dump a list of status records (each with name, status,
task, blocker, updated_at_ms, heartbeat_ms) so CI / smoke conductors
/ peer agents can parse machine-readable state.
"""
from __future__ import annotations

from claudeteam.store import local_facts
from claudeteam.util import ago_ms, pop_bool_flag, print_json


def _emit_text(rows: list[dict], heartbeats: dict[str, int]) -> None:
    if not rows:
        print("👥 no agents have reported status yet")
        return
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


def _emit_json(rows: list[dict], heartbeats: dict[str, int]) -> None:
    """Machine-readable shape: a flat list of status records, one per
    agent that has ever upserted. Heartbeat is folded in alongside so
    consumers don't have to cross-reference two structures."""
    out = [
        {
            "agent": r["agent"],
            "status": r["status"],
            "task": r.get("task", ""),
            "blocker": r.get("blocker", ""),
            "updated_at_ms": r.get("updated_at", 0),
            "heartbeat_ms": heartbeats.get(r["agent"], 0),
        }
        for r in rows
    ]
    print_json(out)


def main(argv: list[str]) -> int:
    rest = list(argv)
    as_json = pop_bool_flag(rest, "--json")
    rows = local_facts.list_all_statuses()
    heartbeats = local_facts.all_heartbeats()
    if as_json:
        _emit_json(rows, heartbeats)
    else:
        _emit_text(rows, heartbeats)
    return 0
