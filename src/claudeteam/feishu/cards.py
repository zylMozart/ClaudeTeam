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

    Wraps `content` in `{tag:column, width:weighted, weight:N, elements:
    [{tag:markdown, content}]}` — the v2-compatible column shape used
    by `column_set_3` and `column_set_2` to build grid layouts.
    """
    return {"tag": "column", "width": "weighted", "weight": weight,
            "elements": [{"tag": "markdown", "content": content}]}


def column_set_3(cells: list[str]) -> dict:
    """3-column grid row. Pads with empty cells when fewer than 3 strings
    are passed so the visual grid stays even (matches `feat/messaging-
    fixes-block1`'s `_col_set_3`)."""
    cols = [col_cell(c) for c in cells]
    while len(cols) < 3:
        cols.append(col_cell(" "))
    return {"tag": "column_set", "flex_mode": "none",
            "background_style": "default", "columns": cols}


def column_set_2(left: str, right: str, *,
                 left_weight: int = 2, right_weight: int = 3) -> dict:
    """2-column row, weighted 2:3 by default (label : detail layout used
    by main's /usage card). Left typically a `**bold label**`, right
    the colored metric string."""
    return {"tag": "column_set", "flex_mode": "none",
            "background_style": "default", "columns": [
                col_cell(left, weight=left_weight),
                col_cell(right, weight=right_weight),
            ]}


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
    """Card v1 (legacy schema) with top-level `elements` + `config:
    {wide_screen_mode}` — the shape `feat/messaging-fixes-block1` /
    `main`'s `build_usage_card` uses, and the only one that lays out
    `column_set` columns SIDE-BY-SIDE in the Feishu app.

    R172: dropped the v2 (`schema: "2.0"` + `body.elements`) shape we
    used since R166. Boss flagged 2026-05-04: in v2 schema, every
    `column_set` row collapses to a stacked label-then-value pair —
    the "对齐都做不好" complaint. Side-by-side rendering only works
    in v1, despite both schemas advertising column_set support.

    `tag:markdown` elements still work inside v1 columns (verified
    against main's live cards), so we keep the GFM-rich `markdown`
    element + `<font color>` html on each cell. Fenced-block-only
    cards (e.g. /tmux) keep using `simple_card` (still v2) because
    they don't need column alignment.
    """
    return {
        "config": {"wide_screen_mode": True},
        "header": {
            "title": {"tag": "plain_text", "content": title},
            "template": _normalised_color(color),
        },
        "elements": elements or [
            {"tag": "markdown", "content": "(无内容)"}],
    }
