"""Feishu event-subscribe loop: NDJSON line iterator → routed delivery.

The pure event-loop function `process_lines` reads NDJSON lines from an
iterator (fed by `lark-cli event +subscribe --compact` stdout in
production, or a fixture list in tests), parses each into a normalised
event dict, classifies it, and applies the decision.

Returns a tally of (handled, dropped) so callers can log heartbeat.
"""
from __future__ import annotations

import json
from dataclasses import dataclass, field
from typing import Callable, Iterable

from claudeteam.feishu.deliver import apply
from claudeteam.feishu.router import Action, classify_event


@dataclass
class LoopStats:
    handled: int = 0
    dropped: int = 0
    drops_by_reason: dict[str, int] = field(default_factory=dict)
    seen_msg_ids: set[str] = field(default_factory=set)


def _normalise(raw: dict) -> dict:
    """lark-cli --compact emits Feishu event payloads under .event; flatten."""
    if "event" in raw and isinstance(raw["event"], dict):
        ev = dict(raw["event"])
    else:
        ev = dict(raw)
    # Flatten common nested fields lark-cli wraps in their own dicts
    msg = ev.get("message") or {}
    sender = ev.get("sender") or {}
    content = msg.get("content")
    text = ""
    if isinstance(content, str):
        try:
            text = (json.loads(content) or {}).get("text", "")
        except json.JSONDecodeError:
            text = content
    return {
        "message_id": msg.get("message_id", ev.get("message_id", "")),
        "chat_id": msg.get("chat_id", ev.get("chat_id", "")),
        "sender_id": sender.get("sender_id", {}).get("open_id") or ev.get("sender_id", ""),
        "text": text or ev.get("text", ""),
        "msg_type": msg.get("message_type", ev.get("msg_type", "text")),
    }


def process_lines(lines: Iterable[str], *,
                  team_agents: list[str],
                  chat_id: str = "",
                  bot_id: str = "",
                  default_target: str = "manager",
                  apply_fn: Callable = apply,
                  on_progress: Callable | None = None) -> LoopStats:
    """Run the subscribe loop over `lines` (one Feishu event JSON each).

    Designed to be exited by exhausting the iterator.  The production
    daemon wraps a never-ending Popen stdout iterator; tests pass a list.
    """
    stats = LoopStats()
    for raw_line in lines:
        line = raw_line.strip()
        if not line:
            continue
        try:
            payload = json.loads(line)
        except json.JSONDecodeError:
            stats.dropped += 1
            stats.drops_by_reason["bad_json"] = stats.drops_by_reason.get("bad_json", 0) + 1
            continue
        event = _normalise(payload)
        decision = classify_event(
            event,
            team_agents=team_agents,
            chat_id=chat_id,
            bot_id=bot_id,
            seen_msg_ids=stats.seen_msg_ids,
            default_target=default_target,
        )
        if decision.is_drop():
            stats.dropped += 1
            r = decision.reason or "drop"
            stats.drops_by_reason[r] = stats.drops_by_reason.get(r, 0) + 1
            continue
        if decision.msg_id:
            stats.seen_msg_ids.add(decision.msg_id)
        apply_fn(decision)
        stats.handled += 1
        if on_progress is not None:
            on_progress(decision, stats)
    return stats
