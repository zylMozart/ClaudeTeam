"""Tests for feishu/cards.py — pure card-schema builders."""
from __future__ import annotations

from claudeteam.feishu.cards import simple_card, kv_card


def test_simple_card_emits_v1_schema_shape():
    card = simple_card("Hello", "**bold** body")
    assert card["config"] == {"wide_screen_mode": True}
    assert card["header"]["title"]["content"] == "Hello"
    assert card["header"]["title"]["tag"] == "plain_text"
    assert card["header"]["template"] == "blue"  # default
    elements = card["elements"]
    assert len(elements) == 1
    assert elements[0]["tag"] == "div"
    assert elements[0]["text"]["tag"] == "lark_md"
    assert elements[0]["text"]["content"] == "**bold** body"


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
    assert card["elements"][0]["text"]["content"] == " "


def test_kv_card_renders_bold_keys():
    card = kv_card("Status", [("agent", "manager"), ("state", "idle")])
    body = card["elements"][0]["text"]["content"]
    assert "**agent**: manager" in body
    assert "**state**: idle" in body
    # row order preserved
    assert body.index("**agent**") < body.index("**state**")


def test_kv_card_handles_empty_rows():
    """Empty rows list must still produce a valid card (Feishu rejects
    elements: [])."""
    card = kv_card("Empty", [])
    assert card["elements"][0]["text"]["content"] == "_(empty)_"


def test_kv_card_threads_color_through():
    card = kv_card("X", [("a", "b")], color="red")
    assert card["header"]["template"] == "red"
