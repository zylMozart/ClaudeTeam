"""Tests for feishu/cards.py — pure card-schema builders."""
from __future__ import annotations

from claudeteam.feishu.cards import (
    beijing_stamp, fenced_block, simple_card,
)


def test_simple_card_emits_v2_schema_shape():
    """R159: card v2 schema — `schema:"2.0"`, `body.elements` list with a
    single `tag:"markdown"` element. v1's `config.wide_screen_mode` and
    nested `text.tag:"lark_md"` are gone; v2's markdown element renders
    fenced code blocks + nested lists which v1 silently dropped."""
    card = simple_card("Hello", "**bold** body")
    assert card["schema"] == "2.0"
    assert card["header"]["title"]["content"] == "Hello"
    assert card["header"]["title"]["tag"] == "plain_text"
    assert card["header"]["template"] == "blue"  # default
    elements = card["body"]["elements"]
    assert len(elements) == 1
    assert elements[0]["tag"] == "markdown"
    assert elements[0]["content"] == "**bold** body"


def test_simple_card_accepts_color_override():
    assert simple_card("X", "y", color="red")["header"]["template"] == "red"
    assert simple_card("X", "y", color="green")["header"]["template"] == "green"


def test_simple_card_falls_back_to_blue_on_unknown_color():
    """Defensive — a typo or future palette change shouldn't bork rendering.

    R166 expanded the palette to include `purple` / `orange` / `turquoise`
    (used by /health server-load card), so use a genuinely-invalid name
    here to keep this test honest."""
    assert simple_card("X", "y", color="magenta")["header"]["template"] == "blue"
    assert simple_card("X", "y", color="")["header"]["template"] == "blue"


def test_simple_card_empty_body_becomes_space():
    """Feishu rejects elements with empty content; coerce to a single space
    so the card schema still validates instead of failing the send."""
    card = simple_card("Title", "")
    assert card["body"]["elements"][0]["content"] == " "


# ── beijing_stamp helper (R117 / R136-relocated) ─────────────────


def test_beijing_stamp_renders_canonical_format():
    """The trailing-stamp helper produces the literal "<YYYY-MM-DD HH:MM>
    北京时间" string used by every card-bearing slash handler."""
    import datetime
    fixed = datetime.datetime(2026, 5, 4, 10, 30)
    assert beijing_stamp(now=lambda: fixed) == "2026-05-04 10:30 北京时间"


def test_beijing_stamp_default_now_is_datetime_now():
    """When no `now` callable is passed, helper uses datetime.now and
    produces a literal-shape string with current local time."""
    s = beijing_stamp()
    assert "北京时间" in s
    # Format `YYYY-MM-DD HH:MM ` shape — verify the dash + colon positions
    assert s[4] == "-" and s[7] == "-"
    assert s[10] == " " and s[13] == ":"


# ── fenced_block helper (R118 / R136-relocated) ──────────────────


def test_fenced_block_wraps_text_in_triple_backticks():
    """Helper for /health, /usage, /tmux body fencing. R159: card v2's
    `markdown` element renders the standard GFM fenced block (triple
    backticks) as a real code block — pre-R159 cards used `lark_md`
    which silently dropped the fence to literal backtick text. Output
    shape is unchanged; just the surrounding card schema swapped."""
    assert fenced_block("alpha\nbeta") == "```\nalpha\nbeta\n```"
    # Empty string still produces a valid fence (Feishu rejects empty
    # element content; an empty fence renders as a 1-line empty code
    # block, harmless)
    assert fenced_block("") == "```\n\n```"


# ── R166: rich card primitives (column_set + colored fonts) ─────


def test_col_cell_wraps_content_in_weighted_column():
    from claudeteam.feishu.cards import col_cell
    cell = col_cell("**hi**", weight=2)
    assert cell["tag"] == "column"
    assert cell["width"] == "weighted"
    assert cell["weight"] == 2
    assert cell["elements"][0]["tag"] == "markdown"
    assert cell["elements"][0]["content"] == "**hi**"


def test_column_set_3_returns_v1_div_lark_md():
    """2026-05-10: emit v1 div+lark_md element (was v2 markdown).
    Server fallbacks v2 markdown to '请升级' disclaimer — same root cause
    as simple_card revert. Cells joined with `\\n\\n` paragraph breaks
    (column_set tag itself doesn't render side-by-side anyway). Empty
    cells dropped so body doesn't end with a stray separator."""
    from claudeteam.feishu.cards import column_set_3
    row = column_set_3(["**CPU**\n0%", "", "**Disk**\n10%"])
    assert row["tag"] == "div"
    assert row["text"]["tag"] == "lark_md"
    assert "**CPU**" in row["text"]["content"]
    assert "**Disk**" in row["text"]["content"]
    assert row["text"]["content"].count("\n\n") == 1


def test_column_set_2_returns_v1_div_lark_md():
    """2026-05-10: same v1 revert as column_set_3. One lark_md line
    `<label>：<value>` (full-width colon) so the bold-label + colored-
    value pair stays on a single visual row."""
    from claudeteam.feishu.cards import column_set_2
    row = column_set_2("**Total**", "<font color='blue'>**$1.23**</font>")
    assert row["tag"] == "div"
    assert row["text"]["tag"] == "lark_md"
    assert "**Total**" in row["text"]["content"]
    assert "$1.23" in row["text"]["content"]
    assert "：" in row["text"]["content"]


def test_load_color_thresholds():
    from claudeteam.feishu.cards import load_color
    # red ≥80, orange ≥50, green <50
    assert load_color(85) == "red"
    assert load_color(80) == "red"
    assert load_color(60) == "orange"
    assert load_color(50) == "orange"
    assert load_color(49) == "green"
    assert load_color(0) == "green"


def test_remaining_color_inverse_thresholds():
    from claudeteam.feishu.cards import remaining_color
    # red ≤20, orange ≤50, green >50
    assert remaining_color(15) == "red"
    assert remaining_color(20) == "red"
    assert remaining_color(35) == "orange"
    assert remaining_color(50) == "orange"
    assert remaining_color(75) == "green"


def test_rich_card_emits_v1_wrapper_with_top_level_elements():
    """2026-05-10: reverted to v1 wrapper (config + flat elements; no
    `schema:"2.0"` / `body` indirection) for the same boss-tenant
    disclaimer fallback that bit simple_card. Pre-built elements list
    passes through verbatim — caller-supplied `tag:"markdown"` /
    `tag:"hr"` / `tag:"div"` all render OK once the OUTER wrapper is
    v1 (probed empirically)."""
    from claudeteam.feishu.cards import rich_card
    elements = [{"tag": "markdown", "content": "hi"}, {"tag": "hr"}]
    card = rich_card("Title", elements, color="purple")
    assert card["config"]["wide_screen_mode"] is True
    assert card["header"]["template"] == "purple"
    assert card["header"]["title"]["content"] == "Title"
    assert card["elements"] == elements
    # Reverse-protect against accidental v2 regression
    assert "schema" not in card
    assert "body" not in card


def test_rich_card_falls_back_to_placeholder_when_elements_empty():
    from claudeteam.feishu.cards import rich_card
    card = rich_card("Title", [], color="blue")
    # v1 placeholder is a div+lark_md (md_element), not v2 markdown
    assert card["elements"][0]["text"]["content"] == "(无内容)"


def test_md_element_returns_v1_div_lark_md():
    """Helper for callers that need to inline a single text block in
    rich_card's elements list (replaces the old v2 `tag:"markdown"`
    inline pattern). Keeps boss-tenant disclaimer at bay."""
    from claudeteam.feishu.cards import md_element
    e = md_element("hi **there**")
    assert e == {"tag": "div", "text": {"tag": "lark_md", "content": "hi **there**"}}


def test_simple_card_accepts_purple_after_R166():
    """R166 added purple to _VALID_COLORS for /health card. Sanity:
    simple_card propagates purple through _normalised_color."""
    from claudeteam.feishu.cards import simple_card
    assert simple_card("X", "y", color="purple")["header"]["template"] == "purple"
