"""ClaudeTeam slash command module.

Public API:
    dispatch(text, ctx) -> (matched: bool, reply)
    SlashContext — dependency container for handlers
"""
from .context import SlashContext
from .dispatch import dispatch

__all__ = ["SlashContext", "dispatch"]
