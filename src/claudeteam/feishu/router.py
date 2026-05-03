"""Pure routing decisions for inbound Feishu events.

Given a Feishu message event dict and the team's agent list, decide:
  - drop: dedup, cross-team, bot self-talk, empty text, etc.
  - route: deliver this text to one or more agents (and identify the sender)

Pure function — no I/O, no globals.  The router daemon (next round)
calls this once per event and acts on the decision.

Drop reasons are stable strings so log filters can grep for them.
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from enum import Enum


class Action(Enum):
    DROP = "drop"
    ROUTE = "route"
    SLASH = "slash"   # operator slash command, dispatched at router-level (zero LLM)
    BROADCAST = "broadcast"  # @team / @all / 全体成员 → every non-sender agent


@dataclass(frozen=True)
class Decision:
    action: Action
    targets: list[str] = field(default_factory=list)   # agents to deliver to
    sender: str = ""                                    # parsed agent sender, if recognised
    text: str = ""                                      # cleaned message text
    msg_id: str = ""
    reason: str = ""                                    # drop reason or "" on route
    create_time: str = ""                               # epoch ms (for catchup cursor)

    def is_drop(self) -> bool:
        return self.action is Action.DROP


# Sender prefix is the bracketed form `[agent]` only.  `@agent` is treated
# as a mention regardless of position (so a human typing `@worker_cc do X`
# routes to worker_cc rather than being misread as worker_cc-as-sender).
_SENDER_RE = re.compile(r"^\s*\[([A-Za-z0-9_\-]+)\]\s*")
_MENTION_RE = re.compile(r"@([A-Za-z0-9_\-]+)")

# Broadcast triggers — match exactly these tokens or 全体 prefix.
_BROADCAST_TOKENS = ("@team", "@all", "@everyone")
_BROADCAST_PREFIX = "全体"   # matches "全体成员", "全体注意" etc.


def _is_broadcast(text: str) -> bool:
    """Detect operator broadcast: `@team` / `@all` / `@everyone` or
    a Chinese 全体X phrase."""
    if not text:
        return False
    if _BROADCAST_PREFIX in text:
        return True
    # Token-aware check (avoid matching @teammate or @allowance)
    for tok in _BROADCAST_TOKENS:
        if re.search(rf"(^|\s){re.escape(tok)}(\s|$|[，。,!?])", text):
            return True
    return False


def _parse_sender(text: str, agents: set[str]) -> tuple[str, str]:
    """If the message starts with `[agent]` and `agent` is on the team,
    strip it and return (agent, remaining_text); else ("", text)."""
    m = _SENDER_RE.match(text)
    if not m or m.group(1) not in agents:
        return "", text
    return m.group(1), text[m.end():].lstrip()


def _parse_mentions(text: str, agents: set[str]) -> list[str]:
    """Return @-mentioned team agents, in order, without duplicates."""
    seen: list[str] = []
    for m in _MENTION_RE.finditer(text):
        name = m.group(1)
        if name in agents and name not in seen:
            seen.append(name)
    return seen


def classify_event(event: dict, *,
                   team_agents: list[str],
                   chat_id: str = "",
                   bot_id: str = "",
                   seen_msg_ids: set[str] | None = None,
                   default_target: str = "manager") -> Decision:
    """Classify one inbound Feishu message event.

    Args:
        event: dict with keys message_id, chat_id, sender_id, text, msg_type
        team_agents: list of agent names known to this deployment
        chat_id: this team's chat — events from other chats get dropped
        bot_id: this app's bot open_id — bot self-talk gets dropped
        seen_msg_ids: optional dedup set; populate as you process
        default_target: agent to route to when no @mention and sender is human

    Decision rules (first match wins):
        no message_id        → DROP "no_msg_id"
        seen msg_id          → DROP "dedup"
        wrong chat_id        → DROP "cross_team"
        sender == bot_id     → DROP "bot_self"
        empty text           → DROP "empty"
        text starts with `/` → SLASH (operator command, zero-LLM dispatch)
        broadcast trigger    → BROADCAST to all non-sender agents
        @mentions hit        → ROUTE to mentioned agents (excluding the sender)
        sender unknown       → ROUTE to [default_target]
        agent broadcast      → DROP "agent_no_target" (no humans to deliver to)
    """
    agents = set(team_agents)
    msg_id = event.get("message_id", "")
    common = {"msg_id": msg_id, "create_time": str(event.get("create_time", ""))}
    if not msg_id:
        return Decision(Action.DROP, reason="no_msg_id", **common)
    if seen_msg_ids is not None and msg_id in seen_msg_ids:
        return Decision(Action.DROP, reason="dedup", **common)
    if chat_id and event.get("chat_id") and event["chat_id"] != chat_id:
        return Decision(Action.DROP, reason="cross_team", **common)
    if bot_id and event.get("sender_id") == bot_id:
        return Decision(Action.DROP, reason="bot_self", **common)

    raw_text = (event.get("text") or "").strip()
    if not raw_text:
        return Decision(Action.DROP, reason="empty", **common)

    # Slash command: matched at router level, NOT injected into any pane.
    # Deliver layer runs the registered handler and posts the result back
    # to chat as a bot reply. Zero LLM involvement.
    #
    # Strip any leading `[<token>]` prefix before the / check — `say.py`
    # always wraps outgoing chat with `[<sender>] <body>`, even when the
    # body is a slash command. Without this strip, `claudeteam say boss
    # "/team"` produces `[boss] /team` in chat and the slash detection
    # misses it entirely (round A2 bug B1).
    slash_text = re.sub(r"^\s*\[[^\]]+\]\s*", "", raw_text)
    if slash_text.startswith("/"):
        return Decision(Action.SLASH, text=slash_text, **common)

    sender, text = _parse_sender(raw_text, agents)
    mentions = [a for a in _parse_mentions(text, agents) if a != sender]

    # Broadcast: hit all non-sender agents (covers "全体成员"/"@team"/"@all"
    # so an operator can address the whole team without listing every name).
    if _is_broadcast(text) and not mentions:
        targets = [a for a in team_agents if a != sender]
        if targets:
            return Decision(Action.BROADCAST, targets=targets, sender=sender,
                            text=text, **common)

    if mentions:
        return Decision(Action.ROUTE, targets=mentions, sender=sender,
                        text=text, **common)

    if not sender:
        # human / unknown sender → manager (or configured default)
        return Decision(Action.ROUTE, targets=[default_target], text=text, **common)

    # agent-tagged message with no @-target → broadcast with nobody to hear it
    return Decision(Action.DROP, sender=sender, text=text,
                    reason="agent_no_target", **common)
