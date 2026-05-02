"""Lazy wake: spawn an agent's CLI on demand.

Two ways an agent's pane can be in a state that's not yet ready to
receive a message:

1. Boss configured the agent as `lazy` in team.json — `claudeteam start`
   created the window but didn't spawn the CLI.  The pane is just a
   shell.
2. The CLI exited — Ctrl-C, /clear, OOM, network blip — and the watchdog
   either hasn't noticed yet or doesn't supervise this pane.

Either way, the next time deliver.apply() wants to inject, it should
detect "no CLI ready" and bring it up.  This module is the detection +
spawn step, kept pure-ish (collaborators injectable for tests).
"""
from __future__ import annotations

import time
from typing import Callable

from claudeteam.agents.base import CliAdapter
from claudeteam.runtime import tmux


def _has_marker(target: tmux.Target, markers: list[str],
                capture: Callable | None) -> bool:
    """Capture the pane (default tmux.capture_pane) and return True iff any
    string in `markers` appears. Empty marker list → always False (saves a
    capture call when the adapter declines to publish that marker class)."""
    if not markers:
        return False
    if capture is None:
        capture = tmux.capture_pane
    text = capture(target, lines=80)
    return any(m in text for m in markers)


def is_ready(target: tmux.Target, adapter: CliAdapter, *,
             capture: Callable | None = None) -> bool:
    """True if the pane already shows one of the adapter's ready markers."""
    return _has_marker(target, adapter.ready_markers(), capture)


def is_rate_limited(target: tmux.Target, adapter: CliAdapter, *,
                    capture: Callable | None = None) -> bool:
    """True if the pane shows any rate-limit marker for this adapter.

    Empty marker list (default for codex/kimi historically) → always False.
    """
    return _has_marker(target, adapter.rate_limit_markers(), capture)


def wake_if_dormant(target: tmux.Target, adapter: CliAdapter, *,
                    spawn_cmd: str,
                    timeout_s: float = 30.0,
                    poll_interval_s: float = 0.5,
                    capture: Callable | None = None,
                    spawn: Callable | None = None,
                    sleep: Callable | None = None,
                    now: Callable | None = None) -> bool:
    """Ensure the agent's CLI is ready to receive input.

    Returns True iff the pane shows a ready marker (already awake, or
    woken in time).  Returns False on timeout — caller decides whether
    to inject anyway, queue, or surface to boss.
    """
    if capture is None:
        capture = tmux.capture_pane
    if spawn is None:
        spawn = tmux.spawn_agent
    if sleep is None:
        sleep = time.sleep
    if now is None:
        now = time.monotonic

    if is_ready(target, adapter, capture=capture):
        return True

    if not spawn(target, spawn_cmd):
        return False

    deadline = now() + timeout_s
    while now() < deadline:
        sleep(poll_interval_s)
        if is_ready(target, adapter, capture=capture):
            return True
    return False
