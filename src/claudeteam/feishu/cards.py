"""Feishu interactive card builders.

Slash handlers return a dict matching Feishu **card v2** schema and
`deliver._apply_slash` sends it via `chat.send_card`
(`--msg-type interactive`) instead of plain text.

Builders are pure: no I/O, no env reads. `simple_card` is the
one-section constructor; `column_set_2/3` + `rich_card` build
multi-section layouts (used by /health and /usage). `beijing_stamp`
and `fenced_block` produce the timestamp suffix and monospace fence
that titles / bodies share.

We're on card v2 (`schema: "2.0"`) because v1's `lark_md` element
silently dropped fenced code blocks and nested lists. v2's
`markdown` element renders the full GFM subset.
"""
from __future__ import annotations

from datetime import datetime
from typing import Callable


# Lark template colors that Feishu's web/mobile app actually renders. These
# are the only ones tested; others (orange, turquoise, etc.) work but
# render varies across mobile/desktop versions.
_VALID_COLORS = ("blue", "green", "red", "yellow", "grey", "purple",
                 "orange", "turquoise")


def _normalised_color(color: str) -> str:
    """Fall back to blue on any unrecognised template color so a typo can't
    bork the whole reply."""
    return color if color in _VALID_COLORS else "blue"


def simple_card(title: str, body: str, *, color: str = "blue") -> dict:
    """Single-section card v2: header + one markdown body element.

    `body` is rendered through Feishu's card v2 `markdown` element, which
    supports a fuller GFM subset than v1's `lark_md` text tag — including
    **fenced code blocks** (triple backticks) and **nested lists**, both
    of which v1 silently degraded to literal text. Empty `body` becomes
    a single space so the element validates.
    """
    return {
        "schema": "2.0",
        "header": {
            "title": {"content": title, "tag": "plain_text"},
            "template": _normalised_color(color),
        },
        "body": {
            "elements": [{"tag": "markdown", "content": body or " "}],
        },
    }


def beijing_stamp(now: Callable[[], datetime] = datetime.now) -> str:
    """Format `now()` as `YYYY-MM-DD HH:MM 北京时间` — the trailing
    suffix every card title uses (manager identity rule that all
    timestamps shown to the boss are in Beijing time)."""
    return f"{now().strftime('%Y-%m-%d %H:%M')} 北京时间"


def fenced_block(text: str) -> str:
    """Wrap `text` in a triple-backtick fence so monospace / box-drawing
    / ANSI artefacts survive Feishu's markdown collapsing (which would
    otherwise eat indentation and merge consecutive spaces)."""
    return f"```\n{text}\n```"


# ── rich card primitives (column_set + colored fonts) ──────


def col_cell(content: str, weight: int = 1) -> dict:
    """Single column cell containing one markdown element.

    Kept for backwards-compat with any external callers; production
    rebuild code path no longer uses this directly — see column_set_2
    / column_set_3 below for the inlined-markdown-row replacement."""
    return {"tag": "column", "width": "weighted", "weight": weight,
            "elements": [{"tag": "markdown", "content": content}]}


def md_element(content: str) -> dict:
    """v1 div with lark_md text. Replaces the v2 `tag:"markdown"`
    element which boss tenant fallbacks to '请升级' disclaimer (cards.py
    R-2026-05-10). Use this for any text-bearing element inside a
    rich_card; v1 lark_md supports the same `**bold**` / `<font color>`
    / inline backtick set we actually use, just no fenced code blocks
    or nested lists (no current caller relies on those)."""
    return {"tag": "div",
            "text": {"tag": "lark_md", "content": content}}


def column_set_3(cells: list[str]) -> dict:
    """3-cell section rendered as one lark_md div with each cell its
    own paragraph (cells separated by `\\n\\n`). Feishu's `column_set`
    tag does not render horizontal-grid in current builds; collapsing
    to paragraphs is the same UX. Empty cells dropped so the body
    doesn't end with a dangling blank."""
    parts = [c for c in cells if c.strip()]
    return md_element("\n\n".join(parts) if parts else " ")


def column_set_2(left: str, right: str, **_legacy_kwargs) -> dict:
    """2-cell row rendered as a single lark_md line `<left>: <right>`.

    Same rationale as `column_set_3`: column_set doesn't render
    side-by-side, collapse to one line. `**Bold**` left labels stay
    bold; right can carry `<font color='…'>` + monospace ` markers.
    """
    return md_element(f"{left}：{right}")


def load_color(pct: int) -> str:
    """Traffic-light color for a load percentage. red≥80, orange≥50,
    green<50. Used for CPU / memory / disk percentages."""
    if pct >= 80:
        return "red"
    if pct >= 50:
        return "orange"
    return "green"


def remaining_color(pct: float) -> str:
    """Inverse of `load_color` — for `<remaining>%` displays where low
    is bad. red≤20, orange≤50, green>50."""
    if pct <= 20:
        return "red"
    if pct <= 50:
        return "orange"
    return "green"


def rich_card(title: str, elements: list, *, color: str = "blue") -> dict:
    """Card v1 with a pre-built top-level `elements` list — for handlers
    that need multi-section layouts (/usage, /health) that
    `simple_card`'s single body can't express.

    Reverted from v2 (`schema:"2.0"` + `body.elements`) on 2026-05-10
    after the same boss-tenant disclaimer fallback that bit simple_card.
    Callers are expected to use `md_element(...)` (or
    `column_set_2/3`) instead of raw `{"tag":"markdown", ...}`; raw
    markdown elements still trigger the fallback inside a v1 wrapper.
    """
    return {
        "config": {"wide_screen_mode": True},
        "header": {
            "title": {"tag": "plain_text", "content": title},
            "template": _normalised_color(color),
        },
        "elements": elements or [md_element("(无内容)")],
    }
