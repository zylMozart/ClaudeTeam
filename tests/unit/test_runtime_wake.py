"""Tests for runtime/wake.py — lazy wake of dormant CLI panes."""
from __future__ import annotations

from claudeteam.runtime import wake, tmux


class _ClaudeFake:
    """Minimal CliAdapter stand-in for tests."""
    def ready_markers(self):
        return ["bypass permissions on", "? for shortcuts"]


def _capturer(text_per_call: list[str]):
    """Return a capture_pane fake that yields one text per call."""
    iterator = iter(text_per_call)

    def fake(target, lines=80):
        try:
            return next(iterator)
        except StopIteration:
            return ""
    return fake


# ── is_ready ─────────────────────────────────────────────────────


def test_is_ready_true_when_pane_shows_marker():
    target = tmux.Target("S", "manager")
    capture = _capturer(["welcome\nbypass permissions on\n>"])
    assert wake.is_ready(target, _ClaudeFake(), capture=capture) is True


def test_is_ready_false_when_pane_blank():
    target = tmux.Target("S", "manager")
    capture = _capturer(["$ "])
    assert wake.is_ready(target, _ClaudeFake(), capture=capture) is False


# ── wake_if_dormant ──────────────────────────────────────────────


def test_wake_returns_true_when_already_ready_no_spawn():
    target = tmux.Target("S", "manager")
    capture = _capturer(["bypass permissions on\n>"])
    spawn_calls = []
    ok = wake.wake_if_dormant(
        target, _ClaudeFake(), spawn_cmd="claude --foo",
        capture=capture,
        spawn=lambda t, c: spawn_calls.append((str(t), c)) or True,
        sleep=lambda s: None,
    )
    assert ok is True
    assert spawn_calls == []


def test_wake_spawns_and_polls_until_ready():
    target = tmux.Target("S", "worker")
    # First check: dormant. Second check (post-spawn): still loading.
    # Third check: ready.
    captures = ["$ ", "$ loading...", "bypass permissions on\n>"]
    capture = _capturer(captures)
    spawn_calls = []
    sleeps = []
    ok = wake.wake_if_dormant(
        target, _ClaudeFake(), spawn_cmd="claude",
        capture=capture,
        spawn=lambda t, c: spawn_calls.append(c) or True,
        sleep=lambda s: sleeps.append(s),
        timeout_s=5.0, poll_interval_s=0.1,
    )
    assert ok is True
    assert spawn_calls == ["claude"]
    assert len(sleeps) == 2  # slept twice while polling


def test_wake_returns_false_when_spawn_fails():
    target = tmux.Target("S", "worker")
    capture = _capturer(["$ "])
    ok = wake.wake_if_dormant(
        target, _ClaudeFake(), spawn_cmd="claude",
        capture=capture,
        spawn=lambda t, c: False,
        sleep=lambda s: None,
    )
    assert ok is False


def test_is_rate_limited_returns_false_when_marker_list_empty():
    target = tmux.Target("S", "agent")

    class NoMarkers:
        def rate_limit_markers(self):
            return []

    capture = lambda t, lines=80: "Approaching usage limit"
    assert wake.is_rate_limited(target, NoMarkers(), capture=capture) is False


def test_is_rate_limited_true_when_pane_shows_marker():
    target = tmux.Target("S", "agent")

    class WithMarkers:
        def rate_limit_markers(self):
            return ["Approaching usage limit"]

    capture = lambda t, lines=80: "...Approaching usage limit\n"
    assert wake.is_rate_limited(target, WithMarkers(), capture=capture) is True


def test_is_rate_limited_false_when_pane_clean():
    target = tmux.Target("S", "agent")

    class WithMarkers:
        def rate_limit_markers(self):
            return ["rate limit"]

    capture = lambda t, lines=80: "all good\n>"
    assert wake.is_rate_limited(target, WithMarkers(), capture=capture) is False


# ── wait_until_ready (no spawn — pure polling) ────────────────────


def test_wait_until_ready_returns_true_immediately_when_already_ready():
    """No-spawn poll variant: if the marker is already there on first
    capture, no sleep happens — the loop checks then exits."""
    target = tmux.Target("S", "manager")
    capture = _capturer(["bypass permissions on\n>"])
    sleeps = []
    ok = wake.wait_until_ready(
        target, _ClaudeFake(), capture=capture,
        sleep=lambda s: sleeps.append(s),
        timeout_s=5.0, poll_interval_s=0.1,
    )
    assert ok is True
    assert sleeps == []  # ready on first check, no sleep needed


def test_wait_until_ready_polls_with_sleep_then_returns_true():
    """When the marker appears on the second capture, exactly one sleep
    fires between the two checks."""
    target = tmux.Target("S", "manager")
    capture = _capturer(["$ ", "bypass permissions on\n>"])
    sleeps = []
    ok = wake.wait_until_ready(
        target, _ClaudeFake(), capture=capture,
        sleep=lambda s: sleeps.append(s),
        timeout_s=5.0, poll_interval_s=0.1,
    )
    assert ok is True
    assert len(sleeps) == 1


def test_wait_until_ready_returns_false_on_timeout():
    """Marker never appears — function returns False after the deadline.
    Uses a fake clock so the test doesn't actually sleep through 20s."""
    target = tmux.Target("S", "manager")
    capture = lambda t, lines=80: "$ "  # always dormant
    clock = {"t": 0.0}

    def now():
        clock["t"] += 0.5
        return clock["t"]

    ok = wake.wait_until_ready(
        target, _ClaudeFake(), capture=capture,
        sleep=lambda s: None, now=now,
        timeout_s=1.0, poll_interval_s=0.1,
    )
    assert ok is False


def test_wake_returns_false_on_timeout():
    target = tmux.Target("S", "worker")
    # always dormant
    capture = lambda t, lines=80: "$ "
    # fake clock: each call advances by 0.5s; deadline is 1.0s.
    clock = {"t": 0.0}

    def now():
        clock["t"] += 0.5
        return clock["t"]

    ok = wake.wake_if_dormant(
        target, _ClaudeFake(), spawn_cmd="claude",
        capture=capture,
        spawn=lambda t, c: True,
        sleep=lambda s: None,
        now=now,
        timeout_s=1.0, poll_interval_s=0.1,
    )
    assert ok is False
