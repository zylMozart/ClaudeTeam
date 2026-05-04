"""Feishu interactive card builders.

Slash handlers can return a dict matching the Feishu v1 card schema and
`deliver._apply_slash` will send it via `chat.send_card` (`--msg-type
interactive`) instead of plain text.

Builders cover the common cases — all are pure: no I/O, no env reads.
Two top-level constructors (`simple_card`, `kv_card`) plus two small
helpers (`beijing_stamp`, `fenced_block`) shared across slash handlers
that produce timestamped / monospace card bodies.
"""
from __future__ import annotations

from datetime import datetime
from typing import Callable


# Lark template colors that Feishu's web/mobile app actually renders. These
# are the only ones tested; others (orange, turquoise, etc.) work but
# render varies across mobile/desktop versions.
_VALID_COLORS = ("blue", "green", "red", "yellow", "grey")


def _normalised_color(color: str) -> str:
    """Fall back to blue on any unrecognised template color so a typo can't
    bork the whole reply."""
    return color if color in _VALID_COLORS else "blue"


def simple_card(title: str, body: str, *, color: str = "blue") -> dict:
    """Single-section card: header + one markdown body element.

    `body` is rendered through Feishu's `lark_md` element, which accepts a
    GFM subset (bold, italic, links, code spans, line breaks). No tables,
    no fenced-with-language code blocks. Empty `body` becomes a single
    space so the element validates.
    """
    return {
        "config": {"wide_screen_mode": True},
        "header": {
            "title": {"tag": "plain_text", "content": title},
            "template": _normalised_color(color),
        },
        "elements": [{
            "tag": "div",
            "text": {"tag": "lark_md", "content": body or " "},
        }],
    }


def kv_card(title: str, rows: list[tuple[str, str]], *,
            color: str = "blue") -> dict:
    """Header + key/value list rendered as compact `**k**: v` lines.

    `rows` is `[(key, value), ...]`. Empty list → a single placeholder line
    so the card has at least one element (Feishu rejects elements: []).
    Keys are bolded; values are plain. Both pass through lark_md so they
    can themselves contain inline markdown / emoji glyphs.
    """
    if not rows:
        body = "_(empty)_"
    else:
        body = "\n".join(f"**{k}**: {v}" for k, v in rows)
    return simple_card(title, body, color=color)


def beijing_stamp(now: Callable[[], datetime] = datetime.now) -> str:
    """Format `now()` as `YYYY-MM-DD HH:MM 北京时间` — the trailing
    suffix every card title uses (R85 manager identity 沟通格式 rule).

    Round-117: extracted from 5 slash card handlers. R136: lifted out
    of `slash.py` into `cards.py` (canonical card-builder home) and
    decoupled from SlashContext by taking a `now` callable directly.
    Slash callers pass `ctx.now` at the call site; tests can pin a
    fixed clock the same way.
    """
    return f"{now().strftime('%Y-%m-%d %H:%M')} 北京时间"


def fenced_block(text: str) -> str:
    """Wrap `text` in a triple-backtick lark_md fence so monospace /
    box-drawing / ANSI artefacts survive Feishu's lark_md collapsing
    (which would otherwise eat indentation and merge consecutive spaces).

    Round-118: extracted from 3 card handlers (/health, /usage, /tmux)
    that all do the same `f"```\\n{out}\\n```"` wrap. R136: moved from
    `slash.py` to `cards.py` next to the other card builders. Empty /
    whitespace-only input still produces a valid fence so Feishu doesn't
    reject the card.
    """
    return f"```\n{text}\n```"
