"""Feishu event-subscribe loop: NDJSON line iterator → routed delivery.

The pure event-loop function `process_lines` reads NDJSON lines from an
iterator (fed by `lark-cli event +subscribe --compact` stdout in
production, or a fixture list in tests), parses each into a normalised
event dict, classifies it, and applies the decision.

Returns a tally of (handled, dropped) so callers can log heartbeat.
"""
from __future__ import annotations

import json
from collections import Counter
from dataclasses import dataclass, field
from typing import Callable, Iterable

from claudeteam.feishu.deliver import apply
from claudeteam.feishu.router import classify_event


@dataclass
class LoopStats:
    handled: int = 0
    dropped: int = 0
    drops_by_reason: Counter = field(default_factory=Counter)
    seen_msg_ids: set[str] = field(default_factory=set)


def _normalise(raw: dict) -> dict:
    """Normalize a lark-cli event payload to the flat shape classify_event wants.

    Two shapes seen in the wild:

    * Modern (lark-cli 1.0.21+ with --compact): top-level flat dict —
      `{chat_id, content, sender_id, message_id, message_type, type, ...}`
      where `content` is either a plain string or a JSON-encoded
      `{"text": "..."}`.
    * Legacy / non-compact: Feishu webhook shape wrapped in `{event: {...}}`
      with nested `message: {chat_id, content, ...}` and
      `sender: {sender_id: {open_id: ...}}`. The original rebuild only
      handled this; round 3 smoke proved live lark-cli has switched to
      the flat shape.

    Handle both. For each field, prefer the legacy nested location
    if present (so old fixtures keep working) then fall back to the
    flat top-level field.
    """
    # Unwrap if the payload is webhook-style with .event
    if "event" in raw and isinstance(raw["event"], dict):
        ev = dict(raw["event"])
    else:
        ev = dict(raw)
    msg = ev.get("message") or {}
    sender = ev.get("sender") or {}

    msg_type = (msg.get("message_type")
                or ev.get("message_type")
                or ev.get("msg_type", "text"))

    # Content: legacy puts it under msg.content, modern at ev.content.
    # In either form it might be JSON-encoded ({"text": "..."} for text,
    # {"image_key": "..."} for image, {"file_key": ..., "file_name": ...}
    # for file) or plain text.
    content = msg.get("content") if msg else ev.get("content")
    text = _extract_text(content, msg_type) or ev.get("text", "")

    # sender_type identifies bot vs human. Modern lark-cli payload has
    # `sender_type: "user" | "app"` flat at top; webhook-shape and
    # chat-messages-list both put it inside `sender.sender_type` /
    # `sender.id_type`. Need it for R174 bot-self detection so manager's
    # own cards don't loop back into manager's inbox via catchup
    # (host_smoke 2026-05-06: 7 forward loops before this caught).
    sender_type = (sender.get("sender_type")
                   or sender.get("id_type")
                   or ev.get("sender_type", ""))
    return {
        "message_id": msg.get("message_id") or ev.get("message_id", ""),
        "chat_id": msg.get("chat_id") or ev.get("chat_id", ""),
        "sender_id": (sender.get("sender_id", {}).get("open_id")
                      or sender.get("id")
                      or ev.get("sender_id", "")),
        "sender_type": sender_type,
        "text": text,
        "msg_type": msg_type,
        "create_time": msg.get("create_time") or ev.get("create_time", ""),
    }


def _extract_text(content, msg_type: str) -> str:
    """Reduce a Feishu message content payload to a plain-text representation
    classify_event can route on.

    - text: returns the literal "text" field (or the raw string if not JSON).
    - image: returns "[image: image_key=<key>]" so the message routes
      instead of getting dropped as "empty".
    - file: returns "[file: <file_name>]" or "[file: file_key=<key>]".
    - audio / sticker / unknown: returns "[<msg_type>]" placeholder.

    Workers receiving these placeholders can use the message_id to fetch
    the actual binary via `lark-cli im +messages-resources-download` if
    they need it; the router's job is just to deliver the route + placeholder
    so the worker pane is aware something arrived.
    """
    if not isinstance(content, str):
        return ""
    try:
        data = json.loads(content) or {}
    except json.JSONDecodeError:
        # Plain string content (legacy variant)
        return content
    if msg_type == "image":
        key = data.get("image_key", "")
        return f"[image: image_key={key}]" if key else "[image]"
    if msg_type == "file":
        name = data.get("file_name") or ""
        key = data.get("file_key", "")
        if name and key:
            return f"[file: {name} (file_key={key})]"
        if name:
            return f"[file: {name}]"
        return f"[file: file_key={key}]" if key else "[file]"
    if msg_type == "audio":
        key = data.get("file_key", "")
        return f"[audio: file_key={key}]" if key else "[audio]"
    if msg_type == "sticker":
        key = data.get("file_key", "")
        return f"[sticker: {key}]" if key else "[sticker]"
    # Default: text or unknown — try common .text field, then .content,
    # then leave empty so callers can fall back to ev.get("text").
    return data.get("text") or data.get("content") or ""


def _record_drop(stats: LoopStats, reason: str) -> None:
    stats.dropped += 1
    stats.drops_by_reason[reason] += 1


def process_lines(lines: Iterable[str], *,
                  team_agents: list[str],
                  chat_id: str = "",
                  bot_id: str = "",
                  default_target: str = "manager",
                  apply_fn: Callable = apply,
                  on_progress: Callable | None = None,
                  seen_msg_ids: set[str] | None = None) -> LoopStats:
    """Run the subscribe loop over `lines` (one Feishu event JSON each).

    Designed to be exited by exhausting the iterator.  The production
    daemon wraps a never-ending Popen stdout iterator; tests pass a list.

    `seen_msg_ids` lets the caller seed the dedup set across calls /
    process restarts. Used by the router to persist seen ids to
    state/router.seen.json so catchup-after-restart doesn't re-apply
    messages that were already handled before the restart (host_smoke
    2026-05-06: same /tmux manager card forwarded into manager inbox
    every ~3.5min as router self-restarted).
    """
    stats = LoopStats()
    if seen_msg_ids is not None:
        stats.seen_msg_ids = seen_msg_ids
    for raw_line in lines:
        line = raw_line.strip()
        if not line:
            continue
        try:
            payload = json.loads(line)
        except json.JSONDecodeError:
            _record_drop(stats, "bad_json")
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
            _record_drop(stats, decision.reason or "drop")
            continue
        try:
            apply_fn(decision)
        except Exception as e:
            # apply_fn is deliver.apply in production. It catches its
            # own side-effect failures (inbox write, pane inject, slash
            # reply) and returns flagged DeliveryReport, so this branch
            # only fires on unexpected errors (config corruption mid-
            # daemon, adapter resolution, slash handler typos that
            # escape slash.dispatch's wrapper). Log and continue rather
            # than killing the daemon. Don't mark seen so a retry path
            # (catchup or stream re-receive) can re-attempt later.
            print(f"  ⚠️ apply_fn raised on {decision.msg_id}: {e}")
            _record_drop(stats, "apply_error")
            continue
        # Mark seen ONLY after successful apply. Round-63: previous order
        # added to seen BEFORE apply, which meant a transient apply
        # failure permanently dedup'd the message (no retry possible
        # within the process_lines run). With the new order, retries
        # from catchup/replay can re-process.
        if decision.msg_id:
            stats.seen_msg_ids.add(decision.msg_id)
        stats.handled += 1
        if on_progress is not None:
            try:
                on_progress(decision, stats)
            except Exception as e:
                # on_progress is the catchup-cursor writer in production
                # (commands/router._on_progress → catchup.record_decision
                # → atomic_write_text). Disk full / permission denied
                # / temp tmp.replace() race could raise — that should
                # NOT kill the daemon. Cursor staleness is recoverable
                # on the next event; daemon death is not.
                print(f"  ⚠️ on_progress callback failed on {decision.msg_id}: {e}")
    return stats
