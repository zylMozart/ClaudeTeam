"""Core dispatch function for slash commands.

dispatch(text, ctx) -> (matched: bool, reply: str | dict | None)

All handlers receive SlashContext for dependency injection.
"""
from __future__ import annotations

from .context import SlashContext
from . import help_, tmux_, team, usage, health

# Handler list: each receives (text, ctx) and returns reply or None.
# Order matters — first non-None wins.
_HANDLERS = [
    help_.handle,
    team.handle_team,
    team.handle_stop,
    team.handle_clear,
    usage.handle_usage,
    health.handle_health,
    tmux_.handle_tmux,
    tmux_.handle_send,
    tmux_.handle_compact,
]


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
