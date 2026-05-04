"""Tests for feishu/deliver.py — Decision → side-effects."""
from __future__ import annotations


from helpers import isolated_env, tmux_patch
from claudeteam.feishu.deliver import apply
from claudeteam.feishu.router import Action, Decision
from claudeteam.store import local_facts


class _FakeAdapter:
    def submit_keys(self):
        return ["Enter"]

    def spawn_cmd(self, agent, model):
        return f"fake-cli {agent} {model}"

    def ready_markers(self):
        return ["fake-ready"]

    def rate_limit_markers(self):
        return []


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


# ── lazy wake integration ──────────────────────────────────────


_WAKE_TEAM = {"agents": {"worker_a": {"cli": "claude-code", "model": "opus"}}}


def test_wake_fn_called_per_target_with_spawn_cmd():
    decision = Decision(action=Action.ROUTE, targets=["worker_a"], text="x", msg_id="om")
    wake_calls = []

    def fake_wake(target, adapter, *, spawn_cmd, init_msg=None, on_woken=None):
        wake_calls.append((str(target), spawn_cmd))
        return True

    with isolated_env(team=_WAKE_TEAM):
        apply(
            decision,
            adapter_for_agent=_adapter_factory,
            tmux_inject=lambda *a, **kw: True,
            wake_fn=fake_wake,
            session="S",
        )
    assert len(wake_calls) == 1
    assert wake_calls[0][0] == "S:worker_a"
    assert "worker_a" in wake_calls[0][1]
    assert "opus" in wake_calls[0][1]


def test_wake_fn_returning_false_still_attempts_inject():
    decision = Decision(action=Action.ROUTE, targets=["worker_a"], text="x", msg_id="om")
    inject_calls = []
    with isolated_env(team=_WAKE_TEAM):
        report = apply(
            decision,
            adapter_for_agent=_adapter_factory,
            tmux_inject=lambda *a, **kw: inject_calls.append(a) or True,
            wake_fn=lambda *a, **kw: False,
            session="S",
        )
    assert len(inject_calls) == 1
    assert report.injected == ["worker_a"]


def test_no_wake_fn_skips_wake_step():
    """Backward-compat: deliver without wake_fn does nothing wake-related."""
    decision = Decision(action=Action.ROUTE, targets=["worker_a"], text="x", msg_id="om")
    with isolated_env():
        report = apply(
            decision,
            adapter_for_agent=_adapter_factory,
            tmux_inject=lambda *a, **kw: True,
            session="S",
        )
    assert report.injected == ["worker_a"]


# ── rate limit ──────────────────────────────────────────────────


def test_rate_limited_pane_keeps_inbox_skips_inject():
    """When wake.is_rate_limited returns True for an agent, inbox row is
    written but inject is skipped — message preserved for replay."""
    decision = Decision(action=Action.ROUTE, targets=["worker_a"], text="x", msg_id="om")
    inject_calls = []

    class RateLimitedAdapter:
        def submit_keys(self):
            return ["Enter"]

        def spawn_cmd(self, agent, model):
            return "fake"

        def ready_markers(self):
            return ["fake-ready"]

        def rate_limit_markers(self):
            return ["Approaching usage limit"]

    # patch tmux.capture_pane to feign a rate-limited pane
    rate_text = "...Approaching usage limit\n"
    with tmux_patch(capture_pane=lambda t, lines=80: rate_text), \
            isolated_env(team=_WAKE_TEAM):
        report = apply(
            decision,
            adapter_for_agent=lambda _: RateLimitedAdapter(),
            tmux_inject=lambda *a, **kw: inject_calls.append(a) or True,
            session="S",
        )
    assert report.written == ["worker_a"]
    assert report.injected == []
    assert report.rate_limited == ["worker_a"]
    assert inject_calls == []


def test_each_agent_uses_its_own_submit_keys():
    """Codex/Kimi vs Claude submit-key sequences differ; verify each."""
    keys_seen = {}

    class _A:
        def __init__(self, keys):
            self._k = keys

        def submit_keys(self):
            return self._k

        def rate_limit_markers(self):
            return []

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


# ── SLASH dispatch + chat-send failure logging ───────────────────


def test_slash_logs_warning_when_chat_send_returns_none():
    """REGRESSION: when lark-cli timeout / OAuth wall / proxy interference
    makes chat.send_text return None, the slash command silently lost
    its bot reply card. router log should now make this visible."""
    import io
    import contextlib

    decision = Decision(action=Action.SLASH, text="/help",
                        msg_id="om_slash_test", create_time="0")
    # Round-79: /help now returns a card dict; it routes through
    # chat_send_card, not chat_send. Capture both sites so the test still
    # exercises the failure path regardless of which transport the handler
    # picked.
    chat_send_card_calls = []

    def failing_chat_send_card(chat_id, card, **kw):
        chat_send_card_calls.append({"chat_id": chat_id, "card": card, **kw})
        return None  # simulate lark-cli failure

    out = io.StringIO()
    with isolated_env(team={"agents": {"manager": {}}},
                      runtime_config={"chat_id": "oc_x"}), \
            contextlib.redirect_stdout(out):
        report = apply(decision,
                       chat_send_card=failing_chat_send_card,
                       team_agents=["manager"],
                       chat_id="oc_x",
                       profile="prod")
    # send_card was called (slash dispatched + tried to post a card)
    assert len(chat_send_card_calls) == 1
    body = chat_send_card_calls[0]["card"]["body"]["elements"][0]["content"]
    assert "/help" in body or "🆘" in body
    # Warning was logged so operator can grep the daemon log
    log = out.getvalue()
    assert "chat reply for om_slash_test failed to post" in log
