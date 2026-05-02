"""Tests for runtime/tmux.py — fake subprocess.run, verify call sequences."""
from __future__ import annotations

from helpers import FakeProc as _FakeResult  # local alias preserves call-site naming
from claudeteam.runtime.tmux import (
    Target,
    capture_pane,
    has_session,
    has_window,
    inject,
    new_session,
    new_window,
    send_keys,
    send_text,
    spawn_agent,
)


class _Recorder:
    """Captures every fake subprocess call; returns scripted result."""

    def __init__(self, results=None):
        self.calls: list[list[str]] = []
        # `results` can be a list (one per call) or callable(args) -> result
        self._results = results or []
        self._idx = 0

    def __call__(self, args, **kwargs):
        self.calls.append(list(args))
        if callable(self._results):
            return self._results(args)
        if self._idx < len(self._results):
            r = self._results[self._idx]
            self._idx += 1
            return r
        return _FakeResult()


def test_target_str_is_session_colon_window():
    assert str(Target("S", "manager")) == "S:manager"


def test_has_session_returns_true_on_zero_exit():
    rec = _Recorder([_FakeResult(returncode=0)])
    assert has_session("S", run=rec) is True
    assert rec.calls == [["tmux", "has-session", "-t", "S"]]


def test_has_session_returns_false_on_nonzero():
    rec = _Recorder([_FakeResult(returncode=1)])
    assert has_session("S", run=rec) is False


def test_capture_pane_returns_stdout_when_ok():
    rec = _Recorder([_FakeResult(returncode=0, stdout="hello world\n")])
    out = capture_pane(Target("S", "manager"), lines=30, run=rec)
    assert out == "hello world\n"
    assert rec.calls == [
        ["tmux", "capture-pane", "-t", "S:manager", "-p", "-S", "-30"]
    ]


def test_capture_pane_returns_empty_string_on_failure():
    rec = _Recorder([_FakeResult(returncode=1)])
    assert capture_pane(Target("S", "x"), run=rec) == ""


def test_send_text_uses_literal_flag():
    rec = _Recorder([_FakeResult()])
    send_text(Target("S", "m"), "echo $HOME", run=rec)
    assert rec.calls == [["tmux", "send-keys", "-l", "-t", "S:m", "echo $HOME"]]


def test_send_keys_sends_named_keys_without_literal():
    rec = _Recorder([_FakeResult()])
    send_keys(Target("S", "m"), "Enter", "C-c", run=rec)
    assert rec.calls == [["tmux", "send-keys", "-t", "S:m", "Enter", "C-c"]]


def test_new_session_creates_detached_named_window():
    rec = _Recorder([_FakeResult()])
    assert new_session("MyTeam", window="manager", run=rec) is True
    assert rec.calls == [["tmux", "new-session", "-d", "-s", "MyTeam", "-n", "manager"]]


def test_new_window_creates_in_existing_session():
    rec = _Recorder([_FakeResult()])
    new_window(Target("S", "worker_cc"), run=rec)
    assert rec.calls == [["tmux", "new-window", "-t", "S", "-n", "worker_cc"]]


def test_inject_sends_text_then_default_submit_keys_in_order():
    rec = _Recorder()  # all calls return default ok
    sleeps: list[float] = []
    ok = inject(Target("S", "m"), "hello", sleep=sleeps.append, run=rec)
    assert ok is True
    # 1 send_text + 3 default submit keys = 4 calls
    assert len(rec.calls) == 4
    assert rec.calls[0] == ["tmux", "send-keys", "-l", "-t", "S:m", "hello"]
    assert rec.calls[1] == ["tmux", "send-keys", "-t", "S:m", "Enter"]
    assert rec.calls[2] == ["tmux", "send-keys", "-t", "S:m", "C-m"]
    assert rec.calls[3] == ["tmux", "send-keys", "-t", "S:m", "C-j"]
    # one settle per key + one after the literal text
    assert len(sleeps) == 4


def test_inject_uses_custom_submit_keys_for_codex_style():
    rec = _Recorder()
    inject(Target("S", "m"), "x", submit_keys=["M-Enter", "Enter"],
           sleep=lambda _: None, run=rec)
    keys_sent = [c[-1] for c in rec.calls if c[1] == "send-keys"]
    # first call is the text payload; remaining are submit keys
    assert keys_sent[1:] == ["M-Enter", "Enter"]


def test_inject_returns_false_if_send_text_fails():
    rec = _Recorder([_FakeResult(returncode=1)])
    assert inject(Target("S", "m"), "x", sleep=lambda _: None, run=rec) is False


def test_inject_returns_false_if_a_submit_key_fails():
    # text ok, first key ok, second key fails
    rec = _Recorder([_FakeResult(), _FakeResult(), _FakeResult(returncode=1)])
    ok = inject(Target("S", "m"), "x", submit_keys=["Enter", "C-m"],
                sleep=lambda _: None, run=rec)
    assert ok is False


def test_spawn_agent_sends_command_then_enter():
    rec = _Recorder()
    spawn_agent(Target("S", "w"), "claude --model sonnet", run=rec)
    assert rec.calls == [
        ["tmux", "send-keys", "-l", "-t", "S:w", "claude --model sonnet"],
        ["tmux", "send-keys", "-t", "S:w", "Enter"],
    ]


def test_has_window_uses_session_colon_window():
    rec = _Recorder([_FakeResult(returncode=0)])
    has_window(Target("S", "manager"), run=rec)
    assert rec.calls == [["tmux", "has-session", "-t", "S:manager"]]
