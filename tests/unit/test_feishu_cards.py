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


def test_column_set_3_pads_to_three_cells():
    from claudeteam.feishu.cards import column_set_3
    grid = column_set_3(["a"])
    assert grid["tag"] == "column_set"
    assert grid["flex_mode"] == "none"
    assert len(grid["columns"]) == 3
    # Padded cells contain a single space
    assert grid["columns"][1]["elements"][0]["content"] == " "
    assert grid["columns"][2]["elements"][0]["content"] == " "


def test_column_set_2_uses_2_3_weight_by_default():
    from claudeteam.feishu.cards import column_set_2
    row = column_set_2("**label**", "value")
    assert len(row["columns"]) == 2
    assert row["columns"][0]["weight"] == 2
    assert row["columns"][1]["weight"] == 3


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


def test_rich_card_emits_v1_schema_with_top_level_elements():
    """R172: rich_card flipped from v2 (`schema:"2.0"` + `body.elements`)
    back to v1 (`config.wide_screen_mode` + top-level `elements`) so
    column_set rows lay out side-by-side in the Feishu app — the boss-
    flagged "对齐都做不好" was caused by v2 collapsing column_set
    children into stacked paragraphs. simple_card stays v2 because
    fenced-block-only cards (/tmux) don't need column alignment."""
    from claudeteam.feishu.cards import rich_card
    elements = [{"tag": "markdown", "content": "hi"}]
    card = rich_card("Title", elements, color="purple")
    assert card["config"]["wide_screen_mode"] is True
    assert "schema" not in card  # v1 has no schema field
    assert "body" not in card    # elements live at top level in v1
    assert card["header"]["template"] == "purple"
    assert card["header"]["title"]["content"] == "Title"
    assert card["elements"] == elements


def test_rich_card_falls_back_to_placeholder_when_elements_empty():
    from claudeteam.feishu.cards import rich_card
    card = rich_card("Title", [], color="blue")
    assert card["elements"][0]["content"] == "(无内容)"


def test_simple_card_accepts_purple_after_R166():
    """R166 added purple to _VALID_COLORS for /health card. Sanity:
    simple_card propagates purple through _normalised_color."""
    from claudeteam.feishu.cards import simple_card
    assert simple_card("X", "y", color="purple")["header"]["template"] == "purple"
