"""Tests for feishu/deliver.py — Decision → side-effects."""
from __future__ import annotations


from helpers import isolated_env
from claudeteam.feishu.deliver import apply
from claudeteam.feishu.router import Action, Decision
from claudeteam.store import local_facts


class _FakeAdapter:
    def submit_keys(self):
        return ["Enter"]


def _adapter_factory(_agent):
    return _FakeAdapter()


# ── DROP path ─────────────────────────────────────────────────────


def test_drop_decision_is_skipped_with_no_side_effects():
    decision = Decision(action=Action.DROP, reason="dedup")
    inject_calls = []
    write_calls = []
    report = apply(
        decision,
        adapter_for_agent=_adapter_factory,
        tmux_inject=lambda *a, **kw: inject_calls.append((a, kw)) or True,
        append_message=lambda *a, **kw: write_calls.append((a, kw)),
        session="S",
    )
    assert report.skipped is True
    assert inject_calls == []
    assert write_calls == []


# ── ROUTE — happy path ───────────────────────────────────────────


def test_route_writes_inbox_and_injects_for_each_target():
    decision = Decision(
        action=Action.ROUTE,
        targets=["worker_a", "worker_b"],
        sender="manager",
        text="please do X",
        msg_id="om_1",
    )
    inject_calls = []
    with isolated_env():
        report = apply(
            decision,
            adapter_for_agent=_adapter_factory,
            tmux_inject=lambda target, text, submit_keys=None: inject_calls.append((str(target), text, submit_keys)) or True,
            session="S",
        )

    assert report.skipped is False
    assert report.written == ["worker_a", "worker_b"]
    assert report.injected == ["worker_a", "worker_b"]
    assert report.failed_inject == []
    assert {c[0] for c in inject_calls} == {"S:worker_a", "S:worker_b"}
    # default submit_keys come from the adapter
    assert inject_calls[0][2] == ["Enter"]


def test_route_uses_user_as_sender_when_decision_sender_blank():
    """Human messages have sender="" — store should record `from=user`."""
    decision = Decision(action=Action.ROUTE, targets=["manager"], text="hi", msg_id="om_2")
    with isolated_env():
        apply(
            decision,
            adapter_for_agent=_adapter_factory,
            tmux_inject=lambda *a, **kw: True,
            session="S",
        )
        rows = local_facts.list_messages("manager")
        assert len(rows) == 1
        assert rows[0]["from"] == "user"


def test_route_passes_decision_text_into_inbox():
    decision = Decision(action=Action.ROUTE, targets=["worker"], text="hello world", msg_id="om")
    with isolated_env():
        apply(decision, adapter_for_agent=_adapter_factory,
              tmux_inject=lambda *a, **kw: True, session="S")
        rows = local_facts.list_messages("worker")
        assert rows[0]["content"] == "hello world"


# ── partial failure ──────────────────────────────────────────────


def test_inject_failure_keeps_inbox_write_and_records_failure():
    decision = Decision(action=Action.ROUTE, targets=["worker_a"], text="x", msg_id="om")
    with isolated_env():
        report = apply(
            decision,
            adapter_for_agent=_adapter_factory,
            tmux_inject=lambda *a, **kw: False,
            session="S",
        )
    assert report.written == ["worker_a"]
    assert report.injected == []
    assert report.failed_inject == ["worker_a"]


def test_inject_exception_caught_and_recorded():
    decision = Decision(action=Action.ROUTE, targets=["worker_a"], text="x", msg_id="om")

    def boom(*a, **kw):
        raise RuntimeError("tmux dead")

    with isolated_env():
        report = apply(
            decision,
            adapter_for_agent=_adapter_factory,
            tmux_inject=boom,
            session="S",
        )
    assert report.written == ["worker_a"]
    assert report.failed_inject == ["worker_a"]


def test_append_message_exception_skips_inject_for_that_agent():
    decision = Decision(action=Action.ROUTE,
                        targets=["worker_a", "worker_b"],
                        text="x", msg_id="om")
    inject_calls = []

    def bad_append(agent, *a, **kw):
        if agent == "worker_a":
            raise IOError("disk full")
        # fall through to real local_facts for worker_b
        return local_facts.append_message(agent, *a, **kw)

    with isolated_env():
        report = apply(
            decision,
            adapter_for_agent=_adapter_factory,
            tmux_inject=lambda t, *a, **kw: inject_calls.append(str(t)) or True,
            append_message=bad_append,
            session="S",
        )
    assert "worker_a" not in report.written
    assert "worker_b" in report.written
    # only worker_b got injected
    assert inject_calls == ["S:worker_b"]


# ── adapter integration ─────────────────────────────────────────


def test_each_agent_uses_its_own_submit_keys():
    """Codex/Kimi vs Claude submit-key sequences differ; verify each."""
    keys_seen = {}

    class _A:
        def __init__(self, keys):
            self._k = keys

        def submit_keys(self):
            return self._k

    def factory(agent):
        return _A(["M-Enter"]) if agent == "codex_w" else _A(["Enter"])

    decision = Decision(action=Action.ROUTE, targets=["codex_w", "claude_w"],
                        text="x", msg_id="om")
    with isolated_env():
        apply(
            decision,
            adapter_for_agent=factory,
            tmux_inject=lambda target, text, submit_keys=None:
                keys_seen.setdefault(str(target), submit_keys) or True,
            session="S",
        )
    assert keys_seen["S:codex_w"] == ["M-Enter"]
    assert keys_seen["S:claude_w"] == ["Enter"]
