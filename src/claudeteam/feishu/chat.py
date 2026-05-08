"""Feishu chat operations: send_text, send_card.

Identity: callers pick `as_user` (True) vs `as_bot` (False, default).
User identity needs OAuth login on the lark-cli profile; bot identity
needs the app to have im:message scope.

All functions take an optional `lark_run=` callable for tests.
"""
from __future__ import annotations

import json
from typing import Callable

from claudeteam.feishu.lark import call as _real_run


def _as(as_user: bool) -> list[str]:
    """lark-cli identity flag fragment: `--as user` (with OAuth) or
    `--as bot` (with the app's im:message scope)."""
    return ["--as", "user" if as_user else "bot"]


def send_text(chat_id: str, text: str, *, profile: str = "", as_user: bool = False,
              reply_to: str = "", lark_run: Callable = _real_run) -> dict | None:
    """Send a plain-text message to a Feishu chat.

    When `reply_to` is set we route through `im +messages-reply` (which
    takes `--message-id <om_xxx>` to attach as a reply). Otherwise we use
    `+messages-send`. Both subcommands accept the same identity / text
    flags; only the attachment-to-parent-message differs.

    Returns the lark-cli `data` dict (typically `{"chat_id": ..., "message_id": ...}`)
    on success, None on failure.
    """
    if not chat_id:
        return None
    if reply_to:
        args = [
            "im", "+messages-reply",
            "--message-id", reply_to,
            "--text", text,
            *_as(as_user),
        ]
    else:
        args = [
            "im", "+messages-send",
            "--chat-id", chat_id,
            "--text", text,
            *_as(as_user),
        ]
    return lark_run(args, profile=profile)


def send_card(chat_id: str, card: dict, *, profile: str = "", as_user: bool = False,
              lark_run: Callable = _real_run) -> dict | None:
    """Send an interactive card.  `card` is the Feishu card schema (dict)."""
    if not chat_id:
        return None
    args = [
        "im", "+messages-send",
        "--chat-id", chat_id,
        "--msg-type", "interactive",
        "--content", json.dumps(card, ensure_ascii=False),
        *_as(as_user),
    ]
    return lark_run(args, profile=profile)


def list_recent(chat_id: str, *, page_size: int = 20, profile: str = "",
                as_user: bool = True, lark_run: Callable = _real_run) -> list[dict]:
    """List recent messages in a chat (newest-first per Feishu API).

    Returns the `messages` array; defaults to user identity since the
    bot often lacks chat-history read permission.
    """
    if not chat_id:
        return []
    args = [
        "im", "+chat-messages-list",
        "--chat-id", chat_id,
        "--page-size", str(page_size),
        *_as(as_user),
        "--format", "json",
    ]
    data = lark_run(args, profile=profile)
    if not data:
        return []
    return list(data.get("messages") or [])
