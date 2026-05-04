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
    capture = capture or tmux.capture_pane
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


def _poll_until_ready(target: tmux.Target, adapter: CliAdapter, *,
                      timeout_s: float, poll_interval_s: float,
                      capture: Callable, sleep: Callable, now: Callable) -> bool:
    """Loop `is_ready` checks until a ready marker shows up or `timeout_s`
    elapses. Pure poll — no spawn, no side effects, no defaulting (caller
    fills in collaborators)."""
    deadline = now() + timeout_s
    while now() < deadline:
        if is_ready(target, adapter, capture=capture):
            return True
        sleep(poll_interval_s)
    return False


def wait_until_ready(target: tmux.Target, adapter: CliAdapter, *,
                     timeout_s: float = 20.0,
                     poll_interval_s: float = 0.5,
                     capture: Callable | None = None,
                     sleep: Callable | None = None,
                     now: Callable | None = None) -> bool:
    """Poll the pane until a ready marker shows up. Does NOT spawn — use
    after a fresh `tmux.spawn_agent` to wait for the CLI banner before
    the next inject. Returns True if a marker appeared in time.
    """
    return _poll_until_ready(
        target, adapter,
        timeout_s=timeout_s, poll_interval_s=poll_interval_s,
        capture=capture or tmux.capture_pane,
        sleep=sleep or time.sleep,
        now=now or time.monotonic,
    )


def wake_if_dormant(target: tmux.Target, adapter: CliAdapter, *,
                    spawn_cmd: str,
                    init_msg: str | None = None,
                    on_woken: Callable[[], None] | None = None,
                    timeout_s: float = 30.0,
                    poll_interval_s: float = 0.5,
                    capture: Callable | None = None,
                    spawn: Callable | None = None,
                    inject: Callable | None = None,
                    sleep: Callable | None = None,
                    now: Callable | None = None) -> bool:
    """Ensure the agent's CLI is ready to receive input.

    Returns True iff the pane shows a ready marker (already awake, or
    woken in time).  Returns False on timeout — caller decides whether
    to inject anyway, queue, or surface to boss.

    When the function had to actually spawn (pane was dormant on entry)
    AND `init_msg` is provided, it injects the identity/init prompt
    after the CLI shows ready, then calls `on_woken` (typically used
    to flip the agent's status row from "待命" to "进行中").
    """
    capture = capture or tmux.capture_pane
    spawn = spawn or tmux.spawn_agent
    inject = inject or tmux.inject
    sleep = sleep or time.sleep
    now = now or time.monotonic

    if is_ready(target, adapter, capture=capture):
        return True  # already awake — caller already handled identity at start

    if not spawn(target, spawn_cmd):
        return False

    # Give the CLI a beat to boot before checking — the pane was just
    # spawned; an immediate is_ready will always be False and burns a
    # capture-pane call.
    sleep(poll_interval_s)
    if not _poll_until_ready(target, adapter,
                             timeout_s=timeout_s, poll_interval_s=poll_interval_s,
                             capture=capture, sleep=sleep, now=now):
        return False

    # CLI just came up. Feed it the identity init prompt before whatever
    # real message follows, so the agent starts knowing who it is.
    if init_msg:
        inject(target, init_msg, submit_keys=adapter.submit_keys())
        sleep(poll_interval_s)
    if on_woken is not None:
        on_woken()
    return True
