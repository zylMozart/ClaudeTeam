"""End-to-end in-process test of the ACP delivery path.

Full chain, everything real except Feishu and the LLM:

    fake sidecar NDJSON line
      → feishu.subscribe.process_lines      (real)
      → feishu.deliver.apply                (real; runner=acp branch)
      → store/acp_queue row                 (real, isolated tempdir)
      → runtime/acp_host.AgentWorker        (real)
      → tests/fake_acp_agent.py subprocess  (real ACP wire protocol)
      → row ACKed done + transcript + heartbeat + status

This is the offline equivalent of tests/scenarios/acp_runner.md §1/§5.
"""
from __future__ import annotations

import json
import sys
import time
from pathlib import Path

from helpers import isolated_env
from claudeteam.agents.base import CliAdapter
from claudeteam.feishu import subscribe
from claudeteam.feishu.deliver import apply
from claudeteam.runtime import acp_host
from claudeteam.store import acp_queue, local_facts


FAKE = str(Path(__file__).resolve().parents[1] / "fake_acp_agent.py")

# manager runs claude-code with NO runner pin — exercises the "ACP-capable
# CLI defaults to acp" resolution through the real config path.
_TEAM = {
    "session": "AcpSmokeTeam",
    "agents": {
        "manager":     {"cli": "claude-code"},
        "worker_kimi": {"cli": "kimi-code"},   # tmux-runner control group
    },
}


class FakeAcpAdapter(CliAdapter):
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


def _ndjson_event(message_id: str, sender_id: str, text: str,
                  chat_id: str = "oc_acp") -> str:
    return json.dumps({
        "event": {
            "message": {
                "message_id": message_id,
                "chat_id": chat_id,
                "message_type": "text",
                "content": json.dumps({"text": text}),
            },
            "sender": {"sender_id": {"open_id": sender_id}},
        }
    })


def _wait(pred, timeout_s: float = 15.0) -> bool:
    deadline = time.monotonic() + timeout_s
    while time.monotonic() < deadline:
        if pred():
            return True
        time.sleep(0.05)
    return False


def test_feishu_event_to_acp_ack_full_chain():
    with isolated_env(team=_TEAM,
                      runtime_config={"chat_id": "oc_acp", "lark_profile": ""}) as tmp:
        prompt_log = tmp / "prompts.log"

        # 1. fake sidecar line → router pipeline → deliver
        stats = subscribe.process_lines(
            iter([_ndjson_event("om_1", "ou_boss", "统计仓库文件数并汇报")]),
            team_agents=["manager", "worker_kimi"],
            chat_id="oc_acp",
            default_target="manager",
            apply_fn=apply,
        )
        assert stats.handled == 1

        # inbox row written (canonical record) …
        inbox = local_facts.list_messages("manager")
        assert len(inbox) == 1
        # … and the delivery went to the ACP queue, not a pane
        pending = acp_queue.rows("manager", state=acp_queue.PENDING)
        assert len(pending) == 1
        row = pending[0]
        assert "统计仓库文件数" in row["text"]
        assert row["local_id"] == inbox[0]["local_id"]  # ACK trail joinable
        assert "claudeteam say" in row["text"]           # routing hint intact
        # the tmux control-group agent got nothing
        assert acp_queue.rows("worker_kimi") == []

        # 2. AcpHost worker consumes the row against the fake agent
        w = acp_host.AgentWorker(
            "manager",
            adapter=FakeAcpAdapter({"FAKE_ACP_PROMPT_LOG": str(prompt_log)}),
            log=lambda *a, **k: None)
        w.start()
        try:
            assert _wait(lambda: acp_queue.rows("manager", state=acp_queue.DONE))
        finally:
            w.stop()

        done = acp_queue.rows("manager", state=acp_queue.DONE)[0]
        assert done["stop_reason"] == "end_turn"
        # the agent actually received identity turn 0 + the message
        prompts = prompt_log.read_text(encoding="utf-8")
        assert "统计仓库文件数" in prompts
        # observability side-effects
        assert local_facts.get_heartbeat("manager") is not None
        assert any(l["type"] == "acp_turn"
                   for l in local_facts.list_logs("manager"))


def test_router_crash_between_enqueue_and_turn_loses_nothing():
    """The T1 guarantee, offline: a row enqueued (even claimed) by a host
    that dies is completed by the next host."""
    with isolated_env(team=_TEAM,
                      runtime_config={"chat_id": "oc_acp", "lark_profile": ""}) as tmp:
        prompt_log = tmp / "prompts.log"
        subscribe.process_lines(
            iter([_ndjson_event("om_2", "ou_boss", "复述暗号 X-7")]),
            team_agents=["manager", "worker_kimi"],
            chat_id="oc_acp",
            default_target="manager",
            apply_fn=apply,
        )
        # simulate the dead host having claimed the row mid-flight
        assert acp_queue.claim_next("manager") is not None
        assert acp_queue.has_inflight("manager")

        # "watchdog respawns the router" → a fresh worker starts
        w = acp_host.AgentWorker(
            "manager",
            adapter=FakeAcpAdapter({"FAKE_ACP_PROMPT_LOG": str(prompt_log)}),
            log=lambda *a, **k: None)
        w.start()
        try:
            assert _wait(lambda: acp_queue.rows("manager", state=acp_queue.DONE))
        finally:
            w.stop()
        # delivered exactly once to the fresh agent
        prompts = prompt_log.read_text(encoding="utf-8")
        assert prompts.count("X-7") == 1
        assert acp_queue.rows("manager", state=acp_queue.DONE)[0]["stop_reason"] == "end_turn"
