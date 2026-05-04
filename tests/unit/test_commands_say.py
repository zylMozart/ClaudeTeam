"""Tests for `claudeteam say` — Feishu chat send + local mirror."""
from __future__ import annotations

import contextlib

from helpers import attr_patch, env_patch, isolated_env, run_cli
from claudeteam.feishu import chat as feishu_chat
from claudeteam.store import local_facts


def _isolated(chat_id: str = "oc_test", profile: str = ""):
    return isolated_env(
        team={"agents": {"manager": {}}},
        runtime_config={"chat_id": chat_id, "lark_profile": profile},
    )


@contextlib.contextmanager
def _fake_send():
    """Replace feishu_chat.send_text with a recorder."""
    state = {"calls": [], "result": {"message_id": "om_fake"}}

    def fake(chat_id, text, *, profile="", as_user=False, reply_to="", lark_run=None):
        state["calls"].append({
            "chat_id": chat_id, "text": text,
            "profile": profile, "as_user": as_user, "reply_to": reply_to,
        })
        return state["result"]

    with attr_patch(feishu_chat, send_text=fake):
        yield state




def test_say_sends_to_chat_and_logs_locally():
    with _isolated(), _fake_send() as send:
        rc, out, _ = run_cli(["say", "manager", "hello", "world"])
        assert rc == 0
        assert "manager → chat (message_id=om_fake)" in out
        assert send["calls"]
        call = send["calls"][0]
        assert call["chat_id"] == "oc_test"
        assert call["text"] == "[manager] hello world"
        # local mirror written
        logs = local_facts.list_logs("manager")
        assert len(logs) == 1
        assert logs[0]["type"] == "say"
        assert logs[0]["content"] == "hello world"


def test_say_default_identity_is_bot():
    with _isolated(), _fake_send() as send:
        run_cli(["say", "manager", "hi"])
        assert send["calls"][0]["as_user"] is False


def test_say_as_user_flag():
    with _isolated(), _fake_send() as send:
        run_cli(["say", "manager", "hi", "--as", "user"])
        assert send["calls"][0]["as_user"] is True


def test_say_env_var_picks_user_when_no_flag():
    with _isolated(), _fake_send() as send, \
            env_patch(CLAUDETEAM_LARK_SEND_AS="user"):
        run_cli(["say", "manager", "hi"])
        assert send["calls"][0]["as_user"] is True


def test_say_explicit_flag_overrides_env_var():
    with _isolated(), _fake_send() as send, \
            env_patch(CLAUDETEAM_LARK_SEND_AS="user"):
        run_cli(["say", "manager", "hi", "--as", "bot"])
        assert send["calls"][0]["as_user"] is False


def test_say_reply_flag_threads_through():
    with _isolated(), _fake_send() as send:
        run_cli(["say", "manager", "hi", "--reply", "om_parent"])
        assert send["calls"][0]["reply_to"] == "om_parent"


def test_say_no_local_skips_log_write():
    with _isolated(), _fake_send():
        run_cli(["say", "manager", "hi", "--no-local"])
        assert local_facts.list_logs("manager") == []


def test_say_returns_one_when_chat_id_unset():
    with _isolated(chat_id=""), _fake_send():
        rc, _, err = run_cli(["say", "manager", "hi"])
        assert rc == 1
        assert "chat_id not set" in err


def test_say_returns_one_when_lark_returns_none():
    with _isolated(), _fake_send() as send:
        send["result"] = None
        rc, _, err = run_cli(["say", "manager", "hi"])
        assert rc == 1
        assert "Feishu send failed" in err


def test_say_threads_profile():
    with _isolated(profile="prod"), _fake_send() as send:
        run_cli(["say", "manager", "hi"])
        assert send["calls"][0]["profile"] == "prod"


def test_say_zero_or_one_arg_returns_one():
    rc, _, err = run_cli(["say"])
    assert rc == 1
    assert "usage:" in err
    rc, _, err = run_cli(["say", "manager"])
    assert rc == 1
    assert "usage:" in err


# ── --card flag (round-99) ──────────────────────────────────────


@contextlib.contextmanager
def _fake_send_card():
    """Replace feishu_chat.send_card alongside send_text."""
    state = {"text_calls": [], "card_calls": [],
             "result": {"message_id": "om_fake_card"}}

    def fake_text(chat_id, text, **kw):
        state["text_calls"].append({"chat_id": chat_id, "text": text, **kw})
        return {"message_id": "om_fake_text"}

    def fake_card(chat_id, card, **kw):
        state["card_calls"].append({"chat_id": chat_id, "card": card, **kw})
        return state["result"]

    with attr_patch(feishu_chat, send_text=fake_text, send_card=fake_card):
        yield state


def test_say_card_flag_sends_card_not_text():
    """`--card` routes through send_card; send_text isn't touched."""
    with _isolated(), _fake_send_card() as st:
        rc, _, _ = run_cli(["say", "manager", "重要决策已落地", "--card"])
    assert rc == 0
    assert len(st["card_calls"]) == 1
    assert st["text_calls"] == []
    card = st["card_calls"][0]["card"]
    # Title carries the [agent] attribution that the text path did inline
    assert card["header"]["title"]["content"] == "[manager]"
    body = card["body"]["elements"][0]["content"]
    assert "重要决策已落地" in body
    # manager → blue template per _color_for
    assert card["header"]["template"] == "blue"


def test_say_card_for_worker_uses_green_template():
    """Workers (worker_*) get green by convention (status updates),
    manager gets blue."""
    with _isolated(), _fake_send_card() as st:
        run_cli(["say", "worker_cc", "step 1 done", "--card"])
    card = st["card_calls"][0]["card"]
    assert card["header"]["template"] == "green"


def test_say_card_with_reply_warns_and_sends_card_anyway():
    """Cards don't thread; --card + --reply prints a stderr warning
    but still sends the card (rather than failing). Threading is a
    text-only Feishu feature so silently dropping reply_to is the
    least surprising behaviour."""
    with _isolated(), _fake_send_card() as st:
        rc, _, err = run_cli(["say", "manager", "msg", "--card",
                              "--reply", "om_xx"])
    assert rc == 0
    assert "--card ignores --reply" in err
    # Card sent, reply_to NOT in the kwargs passed to send_card
    assert len(st["card_calls"]) == 1
    assert "reply_to" not in st["card_calls"][0]


def test_say_default_still_sends_text_when_no_card_flag():
    """Backward-compat: without --card, behaviour is exactly the
    pre-R99 text path."""
    with _isolated(), _fake_send_card() as st:
        rc, _, _ = run_cli(["say", "manager", "plain text msg"])
    assert rc == 0
    assert len(st["text_calls"]) == 1
    assert st["card_calls"] == []
    assert st["text_calls"][0]["text"] == "[manager] plain text msg"


def test_say_audit_log_failure_does_not_block_chat_send():
    """REGRESSION: audit log write is best-effort. Disk full / permission
    denied / corrupt logs.jsonl should NOT prevent the chat send — the
    boss is waiting for the message to land in the group, audit row
    is secondary."""
    def boom(*a, **kw):
        raise OSError("[Errno 28] No space left on device")

    with _isolated(), _fake_send() as send, \
            attr_patch(local_facts, append_log=boom):
        rc, _, err = run_cli(["say", "manager", "important message"])
    # Chat send still succeeded despite audit failing
    assert rc == 0
    # The Feishu chat got the message
    assert len(send["calls"]) == 1
    # Stderr surfaced the audit-log warning so operator knows
    assert "audit log write failed" in err
