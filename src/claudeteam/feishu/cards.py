"""Feishu interactive card builders.

Slash handlers can return a dict matching the Feishu v1 card schema and
`deliver._apply_slash` will send it via `chat.send_card` (`--msg-type
interactive`) instead of plain text.

Two builders cover the common cases — both are pure: no I/O, no env reads.
"""
from __future__ import annotations


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
