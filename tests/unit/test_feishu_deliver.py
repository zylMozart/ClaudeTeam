"""Tests for feishu/deliver.py — Decision → side-effects."""
from __future__ import annotations


from helpers import isolated_env
from claudeteam.feishu.deliver import (
    apply, _compose_inject_text, _wants_manager_summary,
)
from claudeteam.feishu.router import Action, Decision
from claudeteam.store import local_facts


class _FakeAdapter:
    def submit_keys(self):
        return ["Enter"]

    def spawn_cmd(self, agent, model):
        return f"fake-cli {agent} {model}"

    def ready_markers(self):
        return ["fake-ready"]

    def process_name(self):
        return "fake"

    def auth_slots(self):
        # No managed auth → agent_auth resolves to mode "none" (empty prefix),
        # keeping these wake/spawn tests independent of auth.
        return None


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


# ── retirement gate ─────────────────────────────────────────────


def test_route_to_retired_agent_keeps_inbox_but_skips_pane():
    """A fired agent (status 已停止) still gets its inbox row (recoverable
    via hire) but its pane is never woken/injected — the delivery-path
    half of the 反复自动重启 fix."""
    decision = Decision(action=Action.ROUTE, targets=["worker_fired"],
                        text="ping", msg_id="om")
    inject_calls = []
    wake_calls = []
    with isolated_env():
        local_facts.upsert_status("worker_fired", local_facts.RETIRED_STATUS, "fired")
        report = apply(
            decision,
            adapter_for_agent=_adapter_factory,
            tmux_inject=lambda t, *a, **kw: inject_calls.append(str(t)) or True,
            wake_fn=lambda *a, **kw: wake_calls.append(a) or True,
            session="S",
        )
    assert report.written == ["worker_fired"]   # inbox row kept
    assert report.retired == ["worker_fired"]   # tracked as retired
    assert report.injected == []
    assert inject_calls == []                    # pane never touched
    assert wake_calls == []                      # never tried to wake/revive


def test_route_mixed_retired_and_live_targets():
    """Firing one target doesn't block delivery to a live peer."""
    decision = Decision(action=Action.ROUTE,
                        targets=["worker_fired", "worker_live"],
                        text="x", msg_id="om")
    inject_calls = []
    with isolated_env():
        local_facts.upsert_status("worker_fired", local_facts.RETIRED_STATUS, "fired")
        report = apply(
            decision,
            adapter_for_agent=_adapter_factory,
            tmux_inject=lambda t, *a, **kw: inject_calls.append(str(t)) or True,
            session="S",
        )
    assert set(report.written) == {"worker_fired", "worker_live"}
    assert report.retired == ["worker_fired"]
    assert report.injected == ["worker_live"]
    assert inject_calls == ["S:worker_live"]


# ── adapter integration ─────────────────────────────────────────


# ── lazy wake integration ──────────────────────────────────────


_WAKE_TEAM = {"agents": {"worker_a": {"cli": "claude-code", "model": "opus", "runner": "tmux"}}}


def test_wake_fn_called_per_target_with_spawn_cmd():
    decision = Decision(action=Action.ROUTE, targets=["worker_a"], text="x", msg_id="om")
    wake_calls = []

    def fake_wake(target, adapter, *, spawn_cmd, init_msg=None, on_woken=None,
                  timeout_s=None, **_kw):
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


def test_spawn_cmd_carries_resolved_auth_on_every_wake():
    """TRIGGER PROOF: a configured long-term token reaches the actual spawn
    command (and blanks ANTHROPIC_API_KEY by priority) via agent_auth — i.e.
    auth resolution really fires on the lazy-wake spawn path, not just in
    agent_auth's own unit test."""
    class _Claudeish:
        def submit_keys(self): return ["Enter"]
        def spawn_cmd(self, agent, model): return f"claude {agent} {model}"
        def ready_markers(self): return ["ready"]
        def process_name(self): return "claude"
        def auth_slots(self):
            from claudeteam.agents.base import AuthSlots
            return AuthSlots("CLAUDE_CODE_OAUTH_TOKEN", ("ANTHROPIC_API_KEY",),
                             ".claude/.credentials.json", "CLAUDE_CODE_OAUTH_TOKEN")

    decision = Decision(action=Action.ROUTE, targets=["worker_a"], text="x", msg_id="om")
    captured = []

    def fake_wake(target, adapter, *, spawn_cmd, **_kw):
        captured.append(spawn_cmd)
        return True

    with isolated_env(team=_WAKE_TEAM):
        from claudeteam.runtime import paths
        envp = paths.state_dir() / ".env"
        envp.parent.mkdir(parents=True, exist_ok=True)
        envp.write_text("CLAUDE_CODE_OAUTH_TOKEN=tok\n")
        apply(
            decision,
            adapter_for_agent=lambda _a: _Claudeish(),
            tmux_inject=lambda *a, **kw: True,
            wake_fn=fake_wake,
            session="S",
        )
        assert len(captured) == 1
        # auth is SOURCED from a private file, never typed inline — no
        # secret on the wire / in the agent's context.
        assert "CLAUDE_CODE_OAUTH_TOKEN=tok" not in captured[0]
        spawn_env = (paths.state_dir() / "spawn-env" / "worker_a.sh").read_text()
        assert "CLAUDE_CODE_OAUTH_TOKEN=tok" in spawn_env   # token reached pane env
        assert "ANTHROPIC_API_KEY=" in spawn_env            # api key blanked (token wins)


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


# ── SLASH dispatch + chat-send failure logging ───────────────────


def test_slash_logs_warning_when_chat_send_returns_none():
    """REGRESSION: when lark-cli timeout / OAuth wall / proxy interference
    makes chat.send_text return None, the slash command silently lost
    its bot reply card. router log should now make this visible."""
    import io
    import contextlib

    decision = Decision(action=Action.SLASH, text="/help",
                        msg_id="om_slash_test", create_time="0")
    # /help returns a card dict; it routes through chat_send_card, not
    # chat_send. Capture both sites so the test still exercises the
    # failure path regardless of which transport the handler picked.
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


# ── inject-text composer ─────────────────────────────────────────


def _decision(text, *, sender=""):
    return Decision(action=Action.ROUTE, targets=["worker_cc"],
                     sender=sender, text=text, msg_id="om_x", create_time="0")


def test_compose_inject_text_user_message_says_use_claudeteam_say():
    """Boss / unknown sender → wrapper points at `claudeteam say` (chat
    callback channel). The original text body is preserved verbatim
    after the hint."""
    out = _compose_inject_text("worker_cc", _decision("hello there"))
    assert "claudeteam say worker_cc" in out
    assert "hello there" in out
    assert "[群聊·老板]" in out


def test_compose_inject_text_peer_message_uses_send_back_to_sender():
    """Sender is a known agent (peer message) → reply via `claudeteam
    send <sender>` instead of public say."""
    out = _compose_inject_text(
        "worker_cc", _decision("question for you", sender="manager"))
    assert "claudeteam send manager worker_cc" in out
    assert "question for you" in out
    assert "[同事·manager]" in out


def test_compose_inject_text_includes_local_id_for_mark_read():
    """When deliver knows the inbox row's local_id, the wrapper appends
    `claudeteam read <id>` so the agent clears its inbox after replying."""
    out = _compose_inject_text(
        "worker_cc", _decision("ack me"), local_id="msg_42")
    assert "claudeteam read msg_42" in out


def test_compose_inject_text_omits_read_hint_when_local_id_blank():
    """No local_id → no read hint (e.g. for synthetic dispatches that
    didn't go through inbox append)."""
    out = _compose_inject_text("worker_cc", _decision("ad-hoc"))
    assert "claudeteam read" not in out


def test_compose_inject_text_summary_cue_adds_send_to_manager_hint():
    """When a boss message asks for a summary / 汇总 / report,
    non-manager agents get an extra hint to also `claudeteam send
    manager` so manager's inbox pings (manager pane is blind to chat)."""
    out = _compose_inject_text(
        "worker_cc", _decision("数一下文件数量然后让 manager 汇总"))
    assert "claudeteam send manager worker_cc" in out


def test_compose_inject_text_summary_cue_skipped_for_manager_self():
    """Manager doesn't need to send-to-self when boss asks for a
    summary; the hint is non-manager-only."""
    out = _compose_inject_text(
        "manager", _decision("做个汇总报告"))
    # The base "claudeteam say manager" hint stays
    assert "claudeteam say manager" in out
    # But the extra "send manager" line is suppressed for manager itself
    assert "claudeteam send manager manager" not in out


def test_wants_manager_summary_chinese_cues():
    for cue in ("汇总", "汇报", "总结", "报告"):
        assert _wants_manager_summary(f"做个 {cue} 给我"), cue


def test_wants_manager_summary_no_match():
    assert not _wants_manager_summary("hello there")
    assert not _wants_manager_summary("just ack me")
