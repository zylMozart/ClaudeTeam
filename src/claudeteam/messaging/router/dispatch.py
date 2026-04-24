"""Pure event classification and routing decisions.

classify_event() is fully pure: every I/O dependency is injected as a
callable, making it straightforward to unit-test without mocking modules.

Routing contract (mirrors feishu_router.py handle_event semantics):

  Phase 1 (unconditional): mark heartbeat, record first_event_at
  Phase 2 (filterable):
    DROP  — no msg_id / dedup / cross-team / bot-self / empty text
    SLASH — text matches a slash command (handled inline, not routed to agent)
    ROUTE — deliver text to one or more agents

  Sender-target matrix:
    targets found (@mentions)  → route to each target except the sender
    no targets, sender is user → default route to "manager"
    no targets, sender is agent→ DROP (agent broadcast to nobody, ignore)
"""
from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from typing import List, Optional


class EventAction(Enum):
    DROP = "drop"
    SLASH = "slash"
    ROUTE = "route"


@dataclass
class DispatchResult:
    action: EventAction
    targets: List[str]          # agents to deliver to (ROUTE only)
    sender: Optional[str]       # originating agent name, or None for users
    text: str                   # sanitized message text
    msg_id: str = ""
    reason: str = ""            # drop reason or slash command token


def classify_event(
    event: dict,
    *,
    is_seen: callable,
    is_bot_message: callable,
    chat_id: str,
    sanitize: callable,
    parse_targets: callable,
    parse_sender: callable,
    is_slash: callable,
) -> DispatchResult:
    """Classify one incoming Feishu event into DROP / SLASH / ROUTE.

    All state reads and side-effect-free transformations are provided as
    callables so this function stays pure and directly testable.

    Args:
        event:          Raw event dict from lark-cli --compact stream.
        is_seen:        callable(msg_id) -> bool
        is_bot_message: callable(sender_id) -> bool
        chat_id:        str, this team's chat_id (empty = no filtering)
        sanitize:       callable(text) -> str, strips spawn-command noise
        parse_targets:  callable(text) -> List[str] of @-mentioned agents
        parse_sender:   callable(text) -> Optional[str] agent name
        is_slash:       callable(text) -> bool, True if text is a slash cmd
    """
    msg_id = event.get("message_id", "")
    if not msg_id:
        return DispatchResult(EventAction.DROP, [], None, "", "", "no_msg_id")

    if is_seen(msg_id):
        return DispatchResult(EventAction.DROP, [], None, "", msg_id, "dedup")

    event_chat_id = event.get("chat_id", "")
    if chat_id and event_chat_id and event_chat_id != chat_id:
        return DispatchResult(EventAction.DROP, [], None, "", msg_id, "cross_team")

    sender_id = event.get("sender_id", "")
    if is_bot_message(sender_id):
        return DispatchResult(EventAction.DROP, [], None, "", msg_id, "bot_self")

    raw_text = event.get("text", event.get("content", ""))
    text = sanitize(raw_text)
    if not text:
        return DispatchResult(EventAction.DROP, [], None, "", msg_id, "empty_text")

    if is_slash(text):
        return DispatchResult(EventAction.SLASH, [], None, text, msg_id, "slash")

    sender = parse_sender(text)
    targets = parse_targets(text)

    if targets:
        routable = [t for t in targets if t != sender]
        return DispatchResult(EventAction.ROUTE, routable, sender, text, msg_id)

    if sender is None:
        # Unsolicited user message → default delivery target is manager
        return DispatchResult(EventAction.ROUTE, ["manager"], None, text, msg_id)

    # Agent broadcast with no @-target: nothing to do
    return DispatchResult(EventAction.DROP, [], sender, text, msg_id, "agent_no_target")
