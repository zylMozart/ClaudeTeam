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
    """Defensive — a typo or future palette change shouldn't bork rendering."""
    assert simple_card("X", "y", color="orange")["header"]["template"] == "blue"
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
