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


@dataclass(frozen=True)
class Decision:
    action: Action
    targets: list[str] = field(default_factory=list)   # agents to deliver to
    sender: str = ""                                    # parsed agent sender, if recognised
    text: str = ""                                      # cleaned message text
    msg_id: str = ""
    reason: str = ""                                    # drop reason or "" on route

    def is_drop(self) -> bool:
        return self.action is Action.DROP


# Sender prefix is the bracketed form `[agent]` only.  `@agent` is treated
# as a mention regardless of position (so a human typing `@worker_cc do X`
# routes to worker_cc rather than being misread as worker_cc-as-sender).
_SENDER_RE = re.compile(r"^\s*\[([A-Za-z0-9_\-]+)\]\s*")
_MENTION_RE = re.compile(r"@([A-Za-z0-9_\-]+)")


def _parse_sender(text: str, agents: set[str]) -> tuple[str, str]:
    """If the message starts with `[agent]` and `agent` is on the team,
    strip it and return (agent, remaining_text); else ("", text)."""
    m = _SENDER_RE.match(text)
    if not m:
        return "", text
    name = m.group(1)
    if name not in agents:
        return "", text
    return name, text[m.end():].lstrip()


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
        no message_id     → DROP "no_msg_id"
        seen msg_id       → DROP "dedup"
        wrong chat_id     → DROP "cross_team"
        sender == bot_id  → DROP "bot_self"
        empty text        → DROP "empty"
        @mentions hit     → ROUTE to mentioned agents (excluding the sender)
        sender unknown    → ROUTE to [default_target]
        agent broadcast   → DROP "agent_no_target" (no humans to deliver to)
    """
    agents = set(team_agents)
    msg_id = event.get("message_id", "")
    if not msg_id:
        return Decision(Action.DROP, reason="no_msg_id")
    if seen_msg_ids is not None and msg_id in seen_msg_ids:
        return Decision(Action.DROP, msg_id=msg_id, reason="dedup")
    if chat_id and event.get("chat_id") and event["chat_id"] != chat_id:
        return Decision(Action.DROP, msg_id=msg_id, reason="cross_team")
    if bot_id and event.get("sender_id") == bot_id:
        return Decision(Action.DROP, msg_id=msg_id, reason="bot_self")

    raw_text = (event.get("text") or "").strip()
    if not raw_text:
        return Decision(Action.DROP, msg_id=msg_id, reason="empty")

    sender, text = _parse_sender(raw_text, agents)
    mentions = [a for a in _parse_mentions(text, agents) if a != sender]

    if mentions:
        return Decision(Action.ROUTE, targets=mentions, sender=sender,
                        text=text, msg_id=msg_id)

    if not sender:
        # human / unknown sender → manager (or configured default)
        return Decision(Action.ROUTE, targets=[default_target],
                        text=text, msg_id=msg_id)

    # agent-tagged message with no @-target → broadcast with nobody to hear it
    return Decision(Action.DROP, sender=sender, text=text,
                    msg_id=msg_id, reason="agent_no_target")
