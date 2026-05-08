#!/usr/bin/env python3
"""Tests for ordered list neutralization in Feishu markdown rendering."""
import os
import sys

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(_ROOT, "src"))

from claudeteam.messaging.renderer import neutralize_ordered_lists, render_feishu_markdown

passed = failed = 0


def ok(name):
    global passed
    passed += 1
    print(f"  \u2705 {name}")


def fail(name, msg=""):
    global failed
    failed += 1
    print(f"  \u274c {name}: {msg}")


def test_basic_numbered_list():
    text = "1. First\n2. Second\n3. Third"
    result = neutralize_ordered_lists(text)
    assert "**1.**" in result, result
    assert "**2.**" in result, result
    assert "**3.**" in result, result
    ok("basic_numbered_list")


def test_preserves_code_fence():
    text = "```\n1. code line\n2. code line\n```"
    result = neutralize_ordered_lists(text)
    assert "1. code line" in result, result
    assert "**1.**" not in result, result
    ok("preserves_code_fence")


def test_mixed_fence_and_list():
    text = "```\n1. code\n```\n4. real item"
    result = neutralize_ordered_lists(text)
    assert "1. code" in result, result
    assert "**4.**" in result, result
    ok("mixed_fence_and_list")


def test_non_list_text_unchanged():
    text = "Hello world\nNo numbers here"
    result = neutralize_ordered_lists(text)
    assert result == text, result
    ok("non_list_text_unchanged")


def test_bullet_list_unchanged():
    text = "- item 1\n- item 2"
    result = neutralize_ordered_lists(text)
    assert result == text, result
    ok("bullet_list_unchanged")


def test_number_in_middle_unchanged():
    text = "There are 2. things to do"
    result = neutralize_ordered_lists(text)
    assert result == text, result
    ok("number_in_middle_unchanged")


def test_full_pipeline():
    text = "Report:\n1. Done\n2. In progress\n3. Not started"
    result = render_feishu_markdown(text)
    assert "**1.**" in result, result
    assert "**2.**" in result, result
    ok("full_pipeline")


def test_empty_input():
    assert neutralize_ordered_lists("") == ""
    assert neutralize_ordered_lists(None) == ""
    ok("empty_input")


if __name__ == "__main__":
    for name, fn in list(globals().items()):
        if name.startswith("test_") and callable(fn):
            try:
                fn()
            except AssertionError as e:
                fail(name, str(e))
            except Exception as e:
                fail(name, f"exception: {e}")
    total = passed + failed
    print(f"\nordered list renderer tests: {passed}/{total} passed")
    if failed:
        sys.exit(1)
