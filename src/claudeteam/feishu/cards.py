"""Feishu interactive card builders.

Slash handlers can return a dict matching the Feishu **card v2** schema
and `deliver._apply_slash` will send it via `chat.send_card`
(`--msg-type interactive`) instead of plain text.

Builders are pure: no I/O, no env reads. One constructor (`simple_card`)
plus two helpers (`beijing_stamp`, `fenced_block`) shared across slash
handlers that produce timestamped / monospace card bodies.

R159: migrated from card v1 (`elements: [{tag:"div", text:{tag:"lark_md",
content:...}}]`) to card v2 (`body: {elements: [{tag:"markdown",
content:...}]}`). The v1 `lark_md` text tag did NOT render fenced code
blocks (triple-backtick fences) — three-backticks showed up as literal text.
v2's dedicated `markdown` element renders the full GFM subset including
fenced blocks AND nested lists, validated live in the test_a chat
(message D `om_x100b50b5131ed13cb229d7c5f1c16b0` for fenced, E
`om_x100b50b52c50fcb0b2ad0b2268f202d` for nested list + trailing text).

(R79 also shipped `kv_card` for `**key**: value` listings; R137
removed it — never had a production caller. Add back if a future
handler genuinely needs the shape.)
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


# ── R166: rich card primitives (column_set + colored fonts) ──────


def col_cell(content: str, weight: int = 1) -> dict:
    """Single column cell containing one markdown element.

    Kept for backwards-compat with any external callers; production
    rebuild code path no longer uses this directly — see column_set_2
    / column_set_3 below for the inlined-markdown-row replacement."""
    return {"tag": "column", "width": "weighted", "weight": weight,
            "elements": [{"tag": "markdown", "content": content}]}


def column_set_3(cells: list[str]) -> dict:
    """3-cell section rendered as one markdown element with each cell
    its own paragraph (cells separated by `\\n\\n`).

    R172.b: dropped the `tag:"column_set"` shape because Feishu's
    current renderer collapses multi-column rows into stacked
    paragraphs in both v1 and v2 schemas — boss flagged 2026-05-04
    "对齐都做不好". /health host总览 cells are multi-line
    (header + value + sub-text), so joining with paragraph breaks
    keeps each cell visually distinct without trying to enforce a
    horizontal grid that the renderer ignores anyway. Empty cells
    are dropped so we don't end with a dangling blank.
    """
    parts = [c for c in cells if c.strip()]
    return {"tag": "markdown",
            "content": "\n\n".join(parts) if parts else " "}


def column_set_2(left: str, right: str, **_legacy_kwargs) -> dict:
    """2-cell row rendered as a single markdown line — `<left>: <right>`.

    Same R172.b rationale as `column_set_3`: column_set rendering is
    broken in current Feishu, so we collapse to one line. The colon +
    space separator keeps the label visually distinct from the value
    while staying alignment-proof. `**Bold**` left labels still bold
    naturally in markdown; the right cell can carry `<font color='…'>`
    spans + monospace `\` markers.
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
    `simple_card`'s single-element body can't express.

    R172.b: stays on v2 schema (`schema:"2.0"` + `body.elements`)
    since column_set was dropped — every row is now a plain markdown
    element, and v2 gives us GFM features (fenced blocks, nested
    lists, `<font color>` HTML) that v1 silently degrades. R172.a
    briefly flipped to v1 thinking column_set rendered side-by-side
    in v1 but not v2; reality is neither schema renders column_set
    side-by-side in current Feishu, so the schema-flip was useless
    and we keep v2 for its other wins.
    """
    return {
        "schema": "2.0",
        "header": {
            "title": {"tag": "plain_text", "content": title},
            "template": _normalised_color(color),
        },
        "body": {"elements": elements or [
            {"tag": "markdown", "content": "(无内容)"}]},
    }
