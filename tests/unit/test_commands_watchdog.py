"""Tests for `claudeteam watchdog` daemon's alert wiring.

Mostly covers `_make_alert_fn` — the rest of the daemon (signal handler,
supervise loop, pidlock acquire) is exercised by the existing
test_runtime_watchdog.py.
"""
from __future__ import annotations

from helpers import attr_patch, isolated_env
from claudeteam.commands import watchdog as cmd_watchdog
from claudeteam.feishu import chat as feishu_chat


def test_make_alert_fn_returns_none_when_chat_id_unset():
    """No chat target → no alert fn → supervise gets None default
    (which it tolerates; cooldowns happen but no chat delivery)."""
    with isolated_env():
        assert cmd_watchdog._make_alert_fn() is None


def test_make_alert_fn_sends_red_card_on_cooldown():
    """Round-98: cooldown alert is a red Feishu card (visually distinct
    from /team /health cards) instead of plain text."""
    cards_sent = []

    def fake_send_card(chat_id, card, **kw):
        cards_sent.append({"chat_id": chat_id, "card": card, **kw})
        return {"message_id": "om_alert"}

    with isolated_env(team={"agents": {"manager": {}}},
                      runtime_config={"chat_id": "oc_x",
                                       "lark_profile": "p"}), \
            attr_patch(feishu_chat, send_card=fake_send_card):
        alert = cmd_watchdog._make_alert_fn()
        assert alert is not None
        alert("router", 3, 600)

    assert len(cards_sent) == 1
    sent = cards_sent[0]
    assert sent["chat_id"] == "oc_x"
    assert sent["profile"] == "p"
    assert sent["as_user"] is False
    card = sent["card"]
    assert card["header"]["template"] == "red"
    title = card["header"]["title"]["content"]
    assert "router" in title and "cooldown" in title
    body = card["elements"][0]["text"]["content"]
    assert "router" in body
    assert "600s" in body
    assert "3" in body
    assert "claudeteam health" in body


def test_make_alert_fn_falls_back_to_text_when_card_send_fails():
    """A broken card path mustn't lose the alert — fall back to send_text
    so the operator at least sees something in chat."""
    text_sent = []

    def card_boom(chat_id, card, **kw):
        raise RuntimeError("card schema rejected by Feishu")

    def fake_send_text(chat_id, text, **kw):
        text_sent.append({"chat_id": chat_id, "text": text, **kw})
        return {"message_id": "om_fallback"}

    with isolated_env(team={"agents": {"manager": {}}},
                      runtime_config={"chat_id": "oc_x"}), \
            attr_patch(feishu_chat, send_card=card_boom,
                       send_text=fake_send_text):
        alert = cmd_watchdog._make_alert_fn()
        alert("router", 5, 300)

    assert len(text_sent) == 1
    assert "router" in text_sent[0]["text"]
    assert "300s" in text_sent[0]["text"]


def test_make_alert_fn_uses_lark_profile_from_runtime_config():
    """Profile must thread through send_card so the right bot identity
    sends the alert (not whichever profile happens to be the default)."""
    captured = []

    def fake_send_card(chat_id, card, **kw):
        captured.append(kw.get("profile"))
        return {"message_id": "om_x"}

    with isolated_env(team={"agents": {"manager": {}}},
                      runtime_config={"chat_id": "oc_x",
                                       "lark_profile": "team_alpha"}), \
            attr_patch(feishu_chat, send_card=fake_send_card):
        alert = cmd_watchdog._make_alert_fn()
        alert("router", 1, 60)

    assert captured == ["team_alpha"]
