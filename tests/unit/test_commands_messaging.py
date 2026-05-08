"""Tests for `claudeteam send / inbox / read` commands.

Goes through run_cli([...]) so we exercise the dispatch + handler
contract end-to-end (without spawning a subprocess).
"""
from __future__ import annotations

from helpers import isolated_env, run_cli
from claudeteam.store import local_facts


def test_send_writes_inbox_and_prints_local_id():
    with isolated_env():
        rc, out, err = run_cli(["send", "worker", "manager", "do task X"])
        assert rc == 0, err
        assert "inbox: worker ← manager" in out
        assert "local_id=msg_" in out

        rows = local_facts.list_messages("worker")
        assert len(rows) == 1
        assert rows[0]["content"] == "do task X"
        assert rows[0]["from"] == "manager"


def test_send_touches_sender_heartbeat():
    with isolated_env():
        run_cli(["send", "worker", "manager", "do X"])
        assert local_facts.get_heartbeat("manager") is not None


def test_inbox_touches_agent_heartbeat():
    with isolated_env():
        run_cli(["inbox", "worker"])
        assert local_facts.get_heartbeat("worker") is not None


def test_send_priority_param_threads_through():
    with isolated_env():
        run_cli(["send", "a", "b", "msg", "高"])
        rows = local_facts.list_messages("a")
        assert rows[0]["priority"] == "高"


def test_send_missing_args_returns_one_with_usage_to_stderr():
    rc, out, err = run_cli(["send", "only-one-arg"])
    assert rc == 1
    assert "usage: claudeteam send" in err


def test_send_no_inject_flag_skips_pane_inject_after_R168():
    """R168: `--no-inject` opts out of the new auto-inject behaviour
    so audit-only writes (caller is parking context for later, not
    expecting recipient to act NOW) stay silent. Inbox row still
    written; recipient won't be pinged."""
    with isolated_env():
        rc, out, _ = run_cli(["send", "worker", "manager", "x", "--no-inject"])
        assert rc == 0
        assert "inbox: worker ← manager" in out
        rows = local_facts.list_messages("worker")
        assert len(rows) == 1


def test_send_default_inject_best_effort_when_no_tmux():
    """Without a live tmux session, the inject step is best-effort —
    `has_window` returns False (or the wrapper raises) and the command
    still returns 0 with the inbox row landed. No noisy stderr."""
    with isolated_env():
        rc, out, err = run_cli(["send", "worker", "manager", "x"])
        assert rc == 0
        assert "inbox: worker ← manager" in out
        rows = local_facts.list_messages("worker")
        assert len(rows) == 1


def test_send_skips_wake_for_non_lazy_agent():
    """Boss-flagged 2026-05-06: 给 manager 发消息不需要等他空闲, 直接
    inject 就行 (claude pane stash input buffer 自己处理). 只 lazy 员
    工才走 wake_if_dormant. 验证: 给一个 has_window=False 的 non-lazy
    agent 发消息时, send 既不调 wake.is_ready 也不调 wake_if_dormant."""
    from helpers import attr_patch
    from claudeteam.runtime import wake, tmux
    from claudeteam.commands import send as send_mod
    calls = {"is_ready": 0, "wake_if_dormant": 0}
    def fake_is_ready(*a, **kw):
        calls["is_ready"] += 1
        return True
    def fake_wake(*a, **kw):
        calls["wake_if_dormant"] += 1
    with isolated_env(team={"agents": {"manager": {"cli": "claude-code"}}}):
        with attr_patch(wake, is_ready=fake_is_ready,
                        wake_if_dormant=fake_wake):
            with attr_patch(tmux, has_window=lambda *a, **kw: False):
                rc, _, _ = run_cli(["send", "manager", "boss", "hi"])
    assert rc == 0
    # has_window=False 提前 return 0 → wake 调用 0 次
    assert calls["is_ready"] == 0
    assert calls["wake_if_dormant"] == 0


def test_send_calls_wake_only_for_lazy_agent():
    """Lazy agent: pane 是 placeholder shell 还没 spawn CLI, 必须 wake_
    if_dormant 否则 inject 落到 shell 不是 CLI."""
    from helpers import attr_patch
    from claudeteam.runtime import wake, tmux, lifecycle
    calls = {"is_ready": 0, "wake_if_dormant": 0}
    def fake_is_ready(*a, **kw):
        calls["is_ready"] += 1
        return False  # not ready → triggers wake
    def fake_wake(*a, **kw):
        calls["wake_if_dormant"] += 1
    with isolated_env(team={"agents": {"worker_lazy": {
            "cli": "claude-code", "lazy": True}}}):
        with attr_patch(wake, is_ready=fake_is_ready,
                        wake_if_dormant=fake_wake):
            with attr_patch(tmux,
                            has_window=lambda *a, **kw: True,
                            inject=lambda *a, **kw: None):
                with attr_patch(lifecycle,
                                pane_env_prefix=lambda: "X=Y"):
                    rc, _, _ = run_cli(
                        ["send", "worker_lazy", "manager", "hi"])
    assert rc == 0
    assert calls["is_ready"] == 1
    assert calls["wake_if_dormant"] == 1


def test_inbox_lists_unread_with_local_id_and_returns_zero():
    with isolated_env():
        run_cli(["send", "w", "m", "first"])
        run_cli(["send", "w", "m", "second"])
        rc, out, _ = run_cli(["inbox", "w"])
        assert rc == 0
        assert "📬 w: 2 unread" in out
        assert "first" in out and "second" in out


def test_inbox_empty_prints_no_unread():
    with isolated_env():
        rc, out, _ = run_cli(["inbox", "nobody"])
        assert rc == 0
        assert "📭 nobody: no unread messages" in out


def test_read_marks_then_inbox_drops_it():
    with isolated_env():
        run_cli(["send", "w", "m", "task A"])
        msgs = local_facts.list_messages("w")
        local_id = msgs[0]["local_id"]

        rc, out, _ = run_cli(["read", local_id])
        assert rc == 0
        assert "marked read" in out

        rc, out, _ = run_cli(["inbox", "w"])
        assert rc == 0
        assert "📭 w: no unread messages" in out


def test_read_unknown_id_returns_one():
    with isolated_env():
        rc, _, err = run_cli(["read", "msg_does_not_exist"])
        assert rc == 1
        assert "no such message" in err
