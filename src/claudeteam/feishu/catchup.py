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

Two response shapes seen in the wild from `lark-cli im +chat-messages-list`
(round-56 smoke caught this):
  - older / fixture: `{body: {content: "..."}, create_time: "<epoch-ms>"}`
  - lark-cli 1.0.21 live: `{content: "...", create_time: "2026-05-03 18:53"}`
The shape-normalisation helpers below accept both.
"""
from __future__ import annotations

import datetime as _dt
import json
from typing import Callable, Iterable

from claudeteam.feishu import chat as _chat
from claudeteam.feishu.router import Decision
from claudeteam.runtime import paths
from claudeteam.util import env_str, read_json, write_json


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


def _extract_content(fei_msg: dict) -> str:
    """Pick content out of either lark-cli response shape:

    Live (lark-cli 1.0.21+): `{"content": "<text>"}`
    Older / fixtures: `{"body": {"content": "<text>"}}`

    Falls back to "" if neither is present."""
    body = fei_msg.get("body") or {}
    return body.get("content") or fei_msg.get("content") or ""


def _msg_to_event_line(fei_msg: dict) -> str:
    """Convert a chat-messages-list row into one NDJSON line matching
    `lark-cli event +subscribe --compact` shape."""
    sender = fei_msg.get("sender") or {}
    payload = {
        "event": {
            "message": {
                "message_id": fei_msg.get("message_id", ""),
                "chat_id": fei_msg.get("chat_id", ""),
                "message_type": fei_msg.get("msg_type", "text"),
                "content": _extract_content(fei_msg),
                "create_time": fei_msg.get("create_time", ""),
            },
            "sender": {
                "sender_id": {"open_id": sender.get("id", "")},
            },
        }
    }
    return json.dumps(payload, ensure_ascii=False)


def _to_epoch_ms(create_time: object) -> int:
    """Coerce a chat-messages-list create_time into epoch ms.

    Accepts:
      - int / numeric str: passed through (already epoch ms)
      - "YYYY-MM-DD HH:MM" or "YYYY-MM-DD HH:MM:SS" (lark-cli 1.0.21
        live shape): parsed as local time → epoch ms
    Returns 0 when uninterpretable so `_newer_than` treats the row
    as older than any non-zero cursor (i.e. "skip safely")."""
    if not create_time:
        return 0
    s = str(create_time).strip()
    if s.isdigit():
        return int(s)
    for fmt in ("%Y-%m-%d %H:%M:%S", "%Y-%m-%d %H:%M"):
        try:
            return int(_dt.datetime.strptime(s, fmt).timestamp() * 1000)
        except ValueError:
            continue
    return 0


def _newer_than(messages: Iterable[dict], cursor_create_time: str) -> list[dict]:
    """Filter `messages` to those at-or-after the cursor minute.

    Two precision realms collide here. Cursor is set from the LIVE event
    `create_time` (lark-cli WebSocket → millisecond precision string).
    `messages` come from `chat-messages-list` REST (minute precision
    string like "2026-05-06 14:08", parses to the floor of that minute).
    A strict `>` or even bare `>=` comparison loses the minute the
    cursor is in: cursor 14:08:32.107 vs REST 14:08:00 → REST < cursor
    → every message that shares the cursor's minute is dropped.

    Floor the cutoff to the minute boundary so REST messages in the
    same minute as the cursor are kept. Same-minute messages already
    handled by the live stream get re-applied; in-process `seen_msg_ids`
    dedups within one router run. Across restarts, the cursor message
    itself is re-applied — acceptable, lark WebSocket misses are far
    more common in observed host_smoke runs (2026-05-06).

    Bad/missing create_time (parses to 0) gets dropped — never include
    rows we can't timestamp, even when there's no cursor.
    """
    raw_cutoff = _to_epoch_ms(cursor_create_time)
    cutoff = (raw_cutoff // 60_000) * 60_000  # floor to minute
    def keep(m: dict) -> bool:
        ts = _to_epoch_ms(m.get("create_time"))
        return ts > 0 and ts >= cutoff
    fresh = [m for m in messages if keep(m)]
    fresh.sort(key=lambda m: _to_epoch_ms(m.get("create_time")))
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
        # Honor CLAUDETEAM_LARK_SEND_AS so bot-only deployments don't trip
        # `need_user_authorization` from `chat-messages-list --as user`
        # (chat.list_recent's historical default). Mirrors `say`'s resolver.
        as_user = env_str("CLAUDETEAM_LARK_SEND_AS").lower() != "bot"
        def list_fn():
            return _chat.list_recent(chat_id, profile=profile,
                                     page_size=page_size, as_user=as_user)
    msgs = list_fn() or []
    fresh = _newer_than(msgs, cursor_ct)
    return [_msg_to_event_line(m) for m in fresh]
