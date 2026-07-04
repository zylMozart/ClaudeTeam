"""Tests for runtime/acp.py — real subprocess round-trips against
tests/fake_acp_agent.py, so the wire protocol (newline JSON-RPC framing,
request pairing, streamed updates, cancel, permission round-trip) is
exercised end-to-end without a real LLM."""
from __future__ import annotations

import sys
import tempfile
import threading
import time
from pathlib import Path

from helpers import env_patch
from claudeteam.runtime.acp import AcpClient, AcpError, default_permission_handler


FAKE = str(Path(__file__).resolve().parents[1] / "fake_acp_agent.py")


def _client(**kwargs) -> AcpClient:
    import os
    env = dict(os.environ)
    env.update(kwargs.pop("extra_env", {}))
    c = AcpClient([sys.executable, FAKE], env=env, **kwargs)
    c.start()
    return c


def test_initialize_and_prompt_round_trip():
    updates: list[dict] = []
    c = _client(on_update=updates.append)
    try:
        init = c.initialize(timeout_s=10)
        assert init["protocolVersion"] == 1
        sid = c.new_session("/tmp", timeout_s=10)
        stop = c.prompt(sid, "你好", timeout_s=10)
        assert stop == "end_turn"
        texts = [u["update"]["content"]["text"] for u in updates
                 if u.get("update", {}).get("sessionUpdate") == "agent_message_chunk"]
        assert texts == ["echo: 你好"]
    finally:
        c.stop()


def test_prompt_delivery_lands_in_prompt_log():
    """The assertion the whole migration hinges on: text handed to
    prompt() reaches the agent verbatim — no submit keys, no escaping."""
    with tempfile.TemporaryDirectory() as tmp:
        log = Path(tmp) / "prompts.log"
        c = _client(extra_env={"FAKE_ACP_PROMPT_LOG": str(log)})
        try:
            c.initialize(timeout_s=10)
            sid = c.new_session("/tmp", timeout_s=10)
            tricky = 'line1\nline2 with "quotes" $HOME `backticks` 中文'
            c.prompt(sid, tricky, timeout_s=10)
            logged = log.read_text(encoding="utf-8").strip()
            assert logged == tricky.replace("\n", "\\n")
        finally:
            c.stop()


def test_cancel_resolves_inflight_prompt_as_cancelled():
    c = _client(extra_env={"FAKE_ACP_TURN_DELAY_S": "5"})
    try:
        c.initialize(timeout_s=10)
        sid = c.new_session("/tmp", timeout_s=10)
        result: dict = {}

        def _turn():
            result["stop"] = c.prompt(sid, "slow work", timeout_s=15)

        t = threading.Thread(target=_turn)
        t.start()
        time.sleep(0.4)  # let the turn start
        c.cancel(sid)
        t.join(timeout=10)
        assert not t.is_alive()
        assert result["stop"] == "cancelled"
    finally:
        c.stop()


def test_permission_request_auto_allowed_by_default():
    updates: list[dict] = []
    c = _client(on_update=updates.append,
                extra_env={"FAKE_ACP_ASK_PERMISSION": "1"})
    try:
        c.initialize(timeout_s=10)
        sid = c.new_session("/tmp", timeout_s=10)
        assert c.prompt(sid, "do the thing", timeout_s=10) == "end_turn"
        texts = [u["update"]["content"]["text"] for u in updates
                 if u.get("update", {}).get("sessionUpdate") == "agent_message_chunk"]
        # fake echoes which option the client picked; default handler
        # prefers allow_always ("always")
        assert "[perm:always]" in texts
    finally:
        c.stop()


def test_default_permission_handler_prefers_allow_always():
    r = default_permission_handler({"options": [
        {"optionId": "o", "kind": "allow_once"},
        {"optionId": "a", "kind": "allow_always"},
    ]})
    assert r["outcome"]["optionId"] == "a"
    r = default_permission_handler({"options": [{"optionId": "r", "kind": "reject_once"}]})
    assert r["outcome"]["outcome"] == "cancelled"


def test_dead_agent_fails_pending_and_new_requests():
    c = _client(extra_env={"FAKE_ACP_DIE_AFTER": "1"})
    try:
        c.initialize(timeout_s=10)
        sid = c.new_session("/tmp", timeout_s=10)
        assert c.prompt(sid, "last words", timeout_s=10) == "end_turn"
        deadline = time.monotonic() + 5
        while c.alive() and time.monotonic() < deadline:
            time.sleep(0.05)
        assert not c.alive()
        try:
            c.prompt(sid, "into the void", timeout_s=2)
            raise AssertionError("expected AcpError on dead agent")
        except AcpError:
            pass
    finally:
        c.stop()


def test_env_patch_unused_guard():
    """Keep the helpers import honest (repo lint: unused imports drift)."""
    with env_patch(CLAUDETEAM_ACP_TEST="1"):
        import os
        assert os.environ["CLAUDETEAM_ACP_TEST"] == "1"
