#!/usr/bin/env python3
"""Unit tests for deterministic supervisor suspend scanner."""
from __future__ import annotations

import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
for p in (ROOT / "scripts", ROOT / "src"):
    if str(p) not in sys.path:
        sys.path.insert(0, str(p))

import supervisor_scan
from claudeteam.runtime.agent_state import AgentState


def state(code="idle", live=True):
    return AgentState(
        agent="worker",
        emoji="✅",
        code=code,
        brief=code,
        confidence="high",
        live_cli=live,
        idle_hint=True,
    )


class Harness:
    def __init__(self, **overrides):
        self.suspended = []
        self.events = []
        self.states = {"worker": state(), "manager": state()}
        self.ages = {"worker": 901, "manager": 901}
        self.queues = {}
        self.unread = {}
        self.status = {}
        self.residual = {}
        self.suspend_returncode = 0
        for k, v in overrides.items():
            setattr(self, k, v)

    def classify(self, agent, session):
        return self.states[agent]

    def pane_age(self, session, agent):
        return self.ages.get(agent)

    def queue_pending(self, agent):
        return self.queues.get(agent, False)

    def inbox_unread(self, agent):
        return self.unread.get(agent, False)

    def status_fn(self, agent):
        return self.status.get(agent)

    def residual_fn(self, session, agent):
        return self.residual.get(agent, False)

    def suspend(self, agent):
        self.suspended.append(agent)
        return subprocess.CompletedProcess(["suspend", agent], self.suspend_returncode, "", "")

    def event(self, event):
        self.events.append(event)

    def scan(self, agents=("worker",), never=frozenset(), threshold=900, dry_run=False):
        return supervisor_scan.scan_once(
            agents=agents,
            session="S",
            idle_threshold=threshold,
            never_suspend=set(never),
            classify_fn=self.classify,
            pane_age_fn=self.pane_age,
            queue_pending_fn=self.queue_pending,
            inbox_unread_fn=self.inbox_unread,
            status_fn=self.status_fn,
            residual_fn=self.residual_fn,
            suspend_fn=self.suspend,
            event_fn=self.event,
            dry_run=dry_run,
        )


def test_idle_agent_over_threshold_suspends():
    h = Harness()
    decisions = h.scan()
    assert decisions[0].action == "suspend"
    assert decisions[0].suspended
    assert h.suspended == ["worker"]
    assert h.events[0]["action"] == "suspend"


def test_manager_default_never_suspend():
    h = Harness()
    decisions = h.scan(agents=("manager",), never={"manager"})
    assert decisions[0].action == "keep"
    assert decisions[0].reason == "never_suspend"
    assert h.suspended == []


def test_busy_state_does_not_suspend():
    h = Harness(states={"worker": state("busy", live=True)})
    decisions = h.scan()
    assert decisions[0].reason == "state_busy"
    assert h.suspended == []


def test_permission_state_does_not_suspend():
    h = Harness(states={"worker": state("permission", live=True)})
    decisions = h.scan()
    assert decisions[0].reason == "state_permission"
    assert h.suspended == []


def test_cli_not_live_does_not_suspend():
    h = Harness(states={"worker": state("cli_not_running", live=False)})
    decisions = h.scan()
    assert decisions[0].reason == "cli_not_live"
    assert h.suspended == []


def test_idle_below_threshold_does_not_suspend():
    h = Harness(ages={"worker": 899})
    decisions = h.scan()
    assert decisions[0].reason == "idle_threshold_not_met"
    assert h.suspended == []


def test_unknown_pane_activity_does_not_suspend():
    h = Harness(ages={"worker": None})
    decisions = h.scan()
    assert decisions[0].reason == "pane_activity_unknown"
    assert h.suspended == []


def test_queue_pending_does_not_suspend():
    h = Harness(queues={"worker": True})
    decisions = h.scan()
    assert decisions[0].reason == "queue_pending"
    assert h.suspended == []


def test_unread_inbox_does_not_suspend():
    h = Harness(unread={"worker": True})
    decisions = h.scan()
    assert decisions[0].reason == "inbox_unread"
    assert h.suspended == []


def test_status_busy_does_not_suspend():
    h = Harness(status={"worker": "进行中"})
    decisions = h.scan()
    assert decisions[0].reason == "status_table_busy"
    assert h.suspended == []


def test_unsubmitted_input_does_not_suspend():
    h = Harness(residual={"worker": True})
    decisions = h.scan()
    assert decisions[0].reason == "unsubmitted_input"
    assert h.suspended == []


def test_dry_run_records_suspend_without_calling_lifecycle():
    h = Harness()
    decisions = h.scan(dry_run=True)
    assert decisions[0].action == "suspend"
    assert not decisions[0].suspended
    assert h.suspended == []
    assert h.events[0]["dry_run"] is True


def test_suspend_failure_keeps_agent():
    h = Harness(suspend_returncode=1)
    decisions = h.scan()
    assert decisions[0].action == "keep"
    assert decisions[0].reason == "suspend_failed"
    assert decisions[0].suspend_returncode == 1


def main() -> int:
    tests = [v for k, v in sorted(globals().items()) if k.startswith("test_")]
    passed = failed = 0
    for fn in tests:
        try:
            fn()
            print(f"  ok {fn.__name__}")
            passed += 1
        except Exception as e:
            print(f"  fail {fn.__name__}: {e}")
            failed += 1
    print(f"\nsupervisor scan tests: {passed}/{passed + failed} passed")
    return 0 if failed == 0 else 1


if __name__ == "__main__":
    raise SystemExit(main())
