"""Tests for feishu/cards.py — pure card-schema builders."""
from __future__ import annotations

from claudeteam.feishu.cards import (
    beijing_stamp, fenced_block, simple_card,
)


def test_simple_card_emits_v1_lark_md_shape():
    """2026-05-10: reverted to v1 schema. v2 (schema:'2.0' + tag:'markdown')
    triggers Feishu server-side fallback — server replaces body with
    '请升级至最新版本客户端，以查看内容' disclaimer instead of rendering
    the real content (3-schema send+GET probe verified). v1 lark_md
    survives unchanged.

    Shape: top-level `config` + `header` + flat `elements` list (no
    `body` wrapper); each element is a `div` with nested `text.tag='lark_md'`.
    """
    card = simple_card("Hello", "**bold** body")
    assert card["config"]["wide_screen_mode"] is True
    assert card["header"]["title"]["tag"] == "plain_text"
    assert card["header"]["title"]["content"] == "Hello"
    assert card["header"]["template"] == "blue"  # default
    elements = card["elements"]
    assert len(elements) == 1
    assert elements[0]["tag"] == "div"
    assert elements[0]["text"]["tag"] == "lark_md"
    assert elements[0]["text"]["content"] == "**bold** body"
    # 反向防回 v2 schema: 'schema' key + 'body' wrapper 不应出现
    assert "schema" not in card
    assert "body" not in card


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
    assert card["elements"][0]["text"]["content"] == " "


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


def test_column_set_3_joins_cells_with_paragraph_breaks():
    """R172.b: column_set rendering is broken in current Feishu (both
    v1 and v2 collapse it to stacked paragraphs anyway), so column_set_3
    now returns a single markdown element with cells separated by `\\n\\n`
    paragraph breaks. Empty/blank cells are dropped."""
    from claudeteam.feishu.cards import column_set_3
    row = column_set_3(["**CPU**\n0%", "", "**Disk**\n10%"])
    assert row["tag"] == "markdown"
    assert "**CPU**" in row["content"]
    assert "**Disk**" in row["content"]
    # Empty cell didn't leak as a stray separator
    assert row["content"].count("\n\n") == 1


def test_column_set_2_joins_label_value_with_colon():
    """R172.b: column_set_2 renders as one markdown line `<label>：<value>`
    (full-width colon) so the bold-label + colored-value pair stays
    on a single visual row."""
    from claudeteam.feishu.cards import column_set_2
    row = column_set_2("**Total**", "<font color='blue'>**$1.23**</font>")
    assert row["tag"] == "markdown"
    assert "**Total**" in row["content"]
    assert "$1.23" in row["content"]
    assert "：" in row["content"]


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


def test_rich_card_emits_v2_schema_with_body_elements():
    """R172.b: rich_card stays on v2 (`schema:"2.0"` + `body.elements`).
    R172.a briefly flipped to v1 thinking column_set rendered side-by-
    side in v1 but not v2; reality is column_set is broken in BOTH
    schemas in current Feishu, so we dropped column_set entirely
    (cards.py column_set_2/3 now emit plain markdown rows) and kept
    v2 for its GFM features (fenced blocks, nested lists, font color
    HTML)."""
    from claudeteam.feishu.cards import rich_card
    elements = [{"tag": "markdown", "content": "hi"}]
    card = rich_card("Title", elements, color="purple")
    assert card["schema"] == "2.0"
    assert card["header"]["template"] == "purple"
    assert card["header"]["title"]["content"] == "Title"
    assert card["body"]["elements"] == elements


def test_rich_card_falls_back_to_placeholder_when_elements_empty():
    from claudeteam.feishu.cards import rich_card
    card = rich_card("Title", [], color="blue")
    assert card["body"]["elements"][0]["content"] == "(无内容)"


def test_simple_card_accepts_purple_after_R166():
    """R166 added purple to _VALID_COLORS for /health card. Sanity:
    simple_card propagates purple through _normalised_color."""
    from claudeteam.feishu.cards import simple_card
    assert simple_card("X", "y", color="purple")["header"]["template"] == "purple"
