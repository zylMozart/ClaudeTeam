"""Router catchup-on-restart.

When the router daemon dies (Ctrl-C, OOM, host reboot), the live
`event +subscribe` stream resumes from the moment we re-attach — any
messages the boss sent during the gap are silently lost.

This module bridges that gap:

* `read_cursor` / `write_cursor` persist the last successfully-classified
  message into `state_dir/router.cursor`.
* `pending_lines` calls `chat-messages-list`, filters to messages newer
  than the cursor, and emits NDJSON lines in the same shape the live
  subscribe loop produces — so `subscribe.process_lines` replays them
  without caring whether the source was a Popen pipe or this catchup.

Cursor advances on every classified Decision (route or drop), so a
crash mid-apply means we re-encounter the message and lean on
process_lines' dedup set to skip duplicates.
"""
from __future__ import annotations

import json
from typing import Callable, Iterable

from claudeteam.feishu import chat as _chat
from claudeteam.feishu.router import Decision
from claudeteam.runtime import paths
from claudeteam.util import read_json, write_json


# ── cursor persistence ─────────────────────────────────────────


def read_cursor() -> dict:
    """Return the persisted cursor or {} (missing / corrupt / blank file)."""
    try:
        return read_json(paths.router_cursor_file(), {})
    except json.JSONDecodeError:
        return {}


def write_cursor(message_id: str, create_time: str) -> None:
    """Persist the last-seen message marker. No-op if either field is empty."""
    if not message_id or not create_time:
        return
    write_json(paths.router_cursor_file(),
               {"message_id": message_id, "create_time": str(create_time)})


def record_decision(decision: Decision) -> None:
    """Advance cursor from a classified Decision (drop or route)."""
    write_cursor(decision.msg_id, decision.create_time)


# ── replay ────────────────────────────────────────────────────


def _msg_to_event_line(fei_msg: dict) -> str:
    """Convert a chat-messages-list row into one NDJSON line matching
    `lark-cli event +subscribe --compact` shape."""
    sender = fei_msg.get("sender") or {}
    body = fei_msg.get("body") or {}
    payload = {
        "event": {
            "message": {
                "message_id": fei_msg.get("message_id", ""),
                "chat_id": fei_msg.get("chat_id", ""),
                "message_type": fei_msg.get("msg_type", "text"),
                "content": body.get("content", ""),
                "create_time": fei_msg.get("create_time", ""),
            },
            "sender": {
                "sender_id": {"open_id": sender.get("id", "")},
            },
        }
    }
    return json.dumps(payload, ensure_ascii=False)


def _newer_than(messages: Iterable[dict], cursor_create_time: str) -> list[dict]:
    cutoff = int(cursor_create_time or 0)
    fresh = []
    for m in messages:
        ct = m.get("create_time")
        try:
            if int(ct or 0) > cutoff:
                fresh.append(m)
        except (TypeError, ValueError):
            continue
    fresh.sort(key=lambda m: int(m.get("create_time") or 0))
    return fresh


def pending_lines(chat_id: str, *,
                  profile: str = "",
                  page_size: int = 50,
                  list_fn: Callable | None = None) -> list[str]:
    """Return NDJSON lines for messages newer than the saved cursor.

    Oldest-first so process_lines applies them in chronological order.
    `list_fn` is injectable for tests; in production it goes through
    `feishu.chat.list_recent`.
    """
    cursor = read_cursor()
    cursor_ct = str(cursor.get("create_time") or "")
    if list_fn is None:
        def list_fn():
            return _chat.list_recent(chat_id, profile=profile, page_size=page_size)
    msgs = list_fn() or []
    fresh = _newer_than(msgs, cursor_ct)
    return [_msg_to_event_line(m) for m in fresh]
