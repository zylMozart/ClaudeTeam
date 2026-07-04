"""Tests for runtime/acp_host.py — AgentWorker against the real fake-agent
subprocess: the full enqueue → claim → prompt → settle(ACK) loop, plus
cancel-in-flight, crash-respawn, and identity turn 0."""
from __future__ import annotations

import sys
import time
from pathlib import Path

from helpers import isolated_env
from claudeteam.agents.base import CliAdapter
from claudeteam.runtime import acp_host
from claudeteam.store import acp_queue, local_facts


FAKE = str(Path(__file__).resolve().parents[1] / "fake_acp_agent.py")

_TEAM = {"agents": {"w": {"cli": "claude-code", "model": "sonnet"}}}


class FakeAcpAdapter(CliAdapter):
    """Adapter whose ACP subprocess is tests/fake_acp_agent.py."""

    def __init__(self, extra_env: dict | None = None):
        self.extra_env = extra_env or {}

    def spawn_cmd(self, agent, model):
        return "true"

    def ready_markers(self):
        return []

    def process_name(self):
        return "python3"

    def acp_argv(self, agent, model):
        return [sys.executable, FAKE]

    def acp_env(self, agent, model):
        return dict(self.extra_env)


def _worker(extra_env: dict | None = None) -> acp_host.AgentWorker:
    return acp_host.AgentWorker("w", adapter=FakeAcpAdapter(extra_env),
                                log=lambda *a, **k: None)


def _wait(pred, timeout_s: float = 10.0) -> bool:
    deadline = time.monotonic() + timeout_s
    while time.monotonic() < deadline:
        if pred():
            return True
        time.sleep(0.05)
    return False


def _identity_stub(tmp: Path):
    """identity.init_prompt reads team config + memory; stub it to keep the
    test focused on the host loop."""
    from helpers import attr_patch
    from claudeteam.agents import identity
    return attr_patch(identity, init_prompt=lambda agent: f"[init {agent}]")


def test_worker_consumes_row_and_acks_with_stop_reason():
    with isolated_env(team=_TEAM) as tmp:
        with _identity_stub(tmp):
            w = _worker()
            qid = acp_queue.enqueue("w", "hello worker", sender="user")
            w.start()
            try:
                assert _wait(lambda: acp_queue.rows("w", state=acp_queue.DONE))
            finally:
                w.stop()
            row = acp_queue.rows("w", state=acp_queue.DONE)[0]
            assert row["qid"] == qid
            assert row["stop_reason"] == "end_turn"
            # ACK trail in logs.jsonl
            logs = local_facts.list_logs("w")
            assert any(l["type"] == "acp_turn" and l["ref"] == qid for l in logs)
            # heartbeat + settled status landed
            assert local_facts.get_heartbeat("w") is not None
            assert local_facts.get_status("w")["status"] == "待命"


def test_identity_init_is_turn_zero_on_fresh_session():
    with isolated_env(team=_TEAM) as tmp:
        log = tmp / "prompts.log"
        with _identity_stub(tmp):
            w = _worker({"FAKE_ACP_PROMPT_LOG": str(log)})
            acp_queue.enqueue("w", "real message")
            w.start()
            try:
                assert _wait(lambda: acp_queue.rows("w", state=acp_queue.DONE))
            finally:
                w.stop()
        prompts = log.read_text(encoding="utf-8").strip().splitlines()
        assert prompts[0].startswith("[init w]")
        assert prompts[1] == "real message"


def test_transcript_captures_streamed_chunks():
    with isolated_env(team=_TEAM) as tmp:
        with _identity_stub(tmp):
            w = _worker()
            acp_queue.enqueue("w", "ping")
            w.start()
            try:
                assert _wait(lambda: acp_queue.rows("w", state=acp_queue.DONE))
            finally:
                w.stop()
        text = acp_host.transcript_file("w").read_text(encoding="utf-8")
        assert "echo: ping" in text
        assert "===== turn" in text  # turn separator for the viewer pane


def test_cancel_control_row_interrupts_inflight_turn():
    with isolated_env(team=_TEAM) as tmp:
        with _identity_stub(tmp):
            w = _worker({"FAKE_ACP_TURN_DELAY_S": "8"})
            acp_queue.enqueue("w", "slow task")
            w.start()
            try:
                # wait for the REAL turn (not the identity turn) to start:
                # _run_turn writes the transcript separator before prompting
                tf = acp_host.transcript_file("w")
                assert _wait(lambda: tf.exists()
                             and "===== turn" in tf.read_text(encoding="utf-8"))
                time.sleep(0.3)  # let session/prompt reach the agent
                acp_queue.enqueue("w", "", kind="cancel")
                for row in acp_queue.take_control_rows("w"):
                    w.handle_control(row)
                assert _wait(lambda: [r for r in acp_queue.rows("w", state=acp_queue.DONE)
                                      if r["kind"] == "prompt"],
                             timeout_s=6)
            finally:
                w.stop()
        done = [r for r in acp_queue.rows("w", state=acp_queue.DONE)
                if r["kind"] == "prompt"]
        assert done[0]["stop_reason"] == "cancelled"


def test_agent_crash_rearms_row_and_respawns():
    """FAKE_ACP_DIE_AFTER=2: identity turn + first prompt kill the process
    mid-loop; the worker must respawn a fresh subprocess and still finish
    the remaining rows (at-least-once delivery across crashes)."""
    with isolated_env(team=_TEAM) as tmp:
        log = tmp / "prompts.log"
        with _identity_stub(tmp):
            w = _worker({"FAKE_ACP_DIE_AFTER": "2",
                         "FAKE_ACP_PROMPT_LOG": str(log)})
            acp_queue.enqueue("w", "first")
            acp_queue.enqueue("w", "second")
            w.start()
            try:
                assert _wait(
                    lambda: len(acp_queue.rows("w", state=acp_queue.DONE)) == 2,
                    timeout_s=20)
            finally:
                w.stop()
        prompts = log.read_text(encoding="utf-8")
        assert "second" in prompts
        # respawn implies a SECOND identity init (fresh session, context lost)
        assert prompts.count("[init w]") == 2


def test_retired_agent_is_not_prompted():
    with isolated_env(team=_TEAM) as tmp:
        with _identity_stub(tmp):
            local_facts.upsert_status("w", local_facts.RETIRED_STATUS, "fired")
            w = _worker()
            acp_queue.enqueue("w", "should not run")
            w.start()
            time.sleep(1.2)
            w.stop()
        assert acp_queue.rows("w", state=acp_queue.DONE) == []
        assert acp_queue.rows("w", state=acp_queue.PENDING) != []
