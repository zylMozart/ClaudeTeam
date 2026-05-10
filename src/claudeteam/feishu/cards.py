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
    """Single-section card v1: header + one lark_md div.

    Reverted from card v2 (`schema:"2.0"` + `tag:"markdown"`) on 2026-05-10
    after probe (3 schemas × send + GET /messages):

      | shape  | server fallback                                |
      |--------|------------------------------------------------|
      | v1     | none — body content survives                   |
      | v2     | '请升级至最新版本客户端，以查看内容' injected   |
      | v2-alt | none — body content survives (lark_md element) |

    Feishu server in the boss's tenant doesn't render the v2 `markdown`
    element; it replaces the message body with the upgrade-prompt
    disclaimer (an img + two text tags), so the boss's client renders
    THAT instead of the actual content. v1 lark_md path is what every
    other in-tenant bot uses — switching back unblocks card display.

    Trade-off: lark_md silently drops fenced code blocks and nested
    lists (the original reason for v2). Cards in this codebase don't
    rely on those — only `/health` and `/usage` use multi-section cards
    (rich_card / column_set_*) which still use card v1 element types
    anyway, so no caller regresses. If a future card needs GFM tables,
    revisit then; for now correctness > markdown breadth.
    """
    return {
        "config": {"wide_screen_mode": True},
        "header": {
            "title": {"tag": "plain_text", "content": title},
            "template": _normalised_color(color),
        },
        "elements": [
            {"tag": "div",
             "text": {"tag": "lark_md", "content": body or " "}},
        ],
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


def column_set_3(cells: list[str]) -> dict:
    """3-cell section rendered as one markdown element with each cell
    its own paragraph (cells separated by `\\n\\n`). Feishu's
    `tag:"column_set"` renders stacked anyway (no real horizontal
    grid), so we collapse to paragraphs and accept the layout. Empty
    cells dropped so the body doesn't end with a dangling blank."""
    parts = [c for c in cells if c.strip()]
    return {"tag": "markdown",
            "content": "\n\n".join(parts) if parts else " "}


def column_set_2(left: str, right: str, **_legacy_kwargs) -> dict:
    """2-cell row rendered as a single markdown line `<left>: <right>`.

    Same rationale as `column_set_3`: Feishu's `column_set` tag does
    not render side-by-side in current builds, so we collapse to one
    line. `**Bold**` left labels stay bold naturally; the right cell
    can carry `<font color='…'>` spans + monospace ` markers.
    """
    return {"tag": "markdown", "content": f"{left}：{right}"}


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
    """Card v2 with a pre-built `body.elements` list — for handlers
    that need multi-section layouts (/usage, /health) that
    `simple_card`'s single-element body can't express. v2 gives us
    GFM features (fenced blocks, nested lists, `<font color>` HTML)
    that v1's `lark_md` silently degrades."""
    return {
        "schema": "2.0",
        "header": {
            "title": {"tag": "plain_text", "content": title},
            "template": _normalised_color(color),
        },
        "body": {"elements": elements or [
            {"tag": "markdown", "content": "(无内容)"}]},
    }
