"""Core dispatch function for slash commands.

dispatch(text, ctx) -> (matched: bool, reply: str | dict | None)

All handlers receive SlashContext for dependency injection.
"""
from __future__ import annotations

import re

from .context import SlashContext
from . import help_, tmux_, team, usage, health

# Handler list: each receives (text, ctx) and returns reply or None.
# Order matters — first non-None wins.
_HANDLERS = [
    help_.handle,
    team.handle_team,
    usage.handle_usage,
    health.handle_health,
    tmux_.handle_tmux,
    tmux_.handle_send,
    tmux_.handle_compact,
    tmux_.handle_stop,
    tmux_.handle_clear,
]

_MATCHERS = [
    r"/help\s*",
    r"/team\s*",
    r"/usage(?:\s+\S+)?\s*",
    r"/health\s*",
    r"/tmux(?:\s+[A-Za-z0-9_-]+)?(?:\s+\d+)?\s*",
    r"/send(?:\s+\S+(?:\s+.+)?)?\s*",
    r"/compact(?:\s+\S+)?\s*",
    r"/stop(?:\s+\S+)?\s*",
    r"/clear(?:\s+\S+)?\s*",
]


def is_slash_command(text: str) -> bool:
    if not text:
        return False
    stripped = text.strip()
    if not stripped.startswith("/"):
        return False
    return any(re.fullmatch(pattern, stripped, re.DOTALL) for pattern in _MATCHERS)


def dispatch(text: str, ctx: SlashContext) -> tuple[bool, object]:
    """Route text to the matching slash handler.

    Returns (matched, reply) where:
      matched=True  → reply is str or dict{"text", "card"}
      matched=False → caller should handle normally
    """
    if not text:
        return (False, None)
    stripped = text.strip()
    if not stripped.startswith("/"):
        return (False, None)
    for handler in _HANDLERS:
        try:
            result = handler(stripped, ctx)
        except Exception as e:
            return (True, f"⚠️ slash command 执行异常：{e}")
        if result is not None:
            return (True, result)
    return (False, None)
