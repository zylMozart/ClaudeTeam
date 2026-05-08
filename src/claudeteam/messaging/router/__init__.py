"""ClaudeTeam messaging router.

Public API:
  RouterState     — mutable daemon state (seen_ids, chat_id, bot_open_id)
  classify_event  — pure classification of one incoming Feishu event
  EventAction     — DROP / SLASH / ROUTE enum
  DispatchResult  — result of classify_event
  handle_event    — full event handler (I/O side-effects via injected fns)
"""
from .state import RouterState, SEEN_IDS_MAX
from .cursor import parse_cursor, load_cursor, save_cursor, refresh_heartbeat, parse_create_time
from .dispatch import classify_event, EventAction, DispatchResult

__all__ = [
    "RouterState",
    "SEEN_IDS_MAX",
    "parse_cursor",
    "load_cursor",
    "save_cursor",
    "refresh_heartbeat",
    "parse_create_time",
    "classify_event",
    "EventAction",
    "DispatchResult",
]
