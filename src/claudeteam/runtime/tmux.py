"""Thin tmux wrapper: capture pane, inject text, manage windows.

Functions take an optional `run=` callable so tests can inject a fake
subprocess runner.  Production callers leave it default (subprocess.run).

Deliberately leaves out the old tmux_utils.py heavy bits (pane-diff idle
classification, `detect_unsubmitted_input_text`, `force_anyway` queue
escalation).  Those land when a concrete consumer needs them.
"""
from __future__ import annotations

import subprocess
import time
from dataclasses import dataclass
from typing import Callable


@dataclass(frozen=True)
class Target:
    session: str
    window: str

    def __str__(self) -> str:
        return f"{self.session}:{self.window}"


class _FailedRun:
    """Stand-in for subprocess.CompletedProcess when the call could
    not be made at all (FileNotFoundError, TimeoutExpired). Mirrors
    the .returncode / .stdout / .stderr trio so `_ok` and friends can
    treat it uniformly as "this tmux op didn't succeed"."""
    __slots__ = ("returncode", "stdout", "stderr")

    def __init__(self, reason: str):
        self.returncode = 1
        self.stdout = ""
        self.stderr = reason


def _default_run(args, **kwargs):
    """subprocess.run wrapper that converts hard failures into a
    soft "rc=1, stderr=<reason>" so callers (has_session, capture_pane,
    inject, ...) just see a normal "tmux op failed" instead of a
    FileNotFoundError traceback bubbling up through every command
    that touches tmux.

    Catches:
      - FileNotFoundError: tmux binary not on PATH (fresh container,
        derived Docker image without tmux). claudeteam health flags
        the missing session red, but the rest of the CLI keeps working.
      - subprocess.TimeoutExpired: tmux server hung (extremely rare;
        most commands return in <50ms). 10s timeout per call.
    """
    try:
        return subprocess.run(args, capture_output=True, text=True, timeout=10, **kwargs)
    except FileNotFoundError:
        return _FailedRun("tmux not found on PATH")
    except subprocess.TimeoutExpired:
        return _FailedRun("tmux command timed out (>10s)")


def _ok(args: list[str], run: Callable) -> bool:
    """Invoke `run(args)` and return True iff returncode == 0. Wraps the
    one-liner pattern every fire-and-forget tmux call needs."""
    return run(args).returncode == 0


def has_session(session: str, *, run: Callable = _default_run) -> bool:
    # `=` forces an exact session-name match. Without it tmux prefix-matches,
    # so with only `ClaudeTeam-other` running, has_session("ClaudeTeam")
    # answered True — health went green against ANOTHER team's session
    # (acceptance F-3). Existence checks gate every downstream tmux call,
    # so exactness here protects the fuzzy-target calls too.
    return _ok(["tmux", "has-session", "-t", f"={session}"], run)


def has_window(target: Target, *, run: Callable = _default_run) -> bool:
    return _ok(["tmux", "has-session", "-t", f"={target}"], run)


def capture_pane(target: Target, *, lines: int = 80, run: Callable = _default_run) -> str:
    r = run(["tmux", "capture-pane", "-t", str(target), "-p", "-S", f"-{lines}"])
    return r.stdout if r.returncode == 0 else ""


def pane_command(target: Target, *, run: Callable = _default_run) -> str:
    """The pane's foreground process name (tmux `#{pane_current_command}`).

    This is what the kernel says is running in the pane — `node` (claude /
    codex / gemini / qwen), `python` / `uv` (kimi), or a shell (`bash` /
    `zsh` …) once the CLI has exited. It's a process fact, not pane content,
    so it's the dependable signal for "is the CLI still up?" without scraping
    the TUI. Returns '' if the pane / window / session doesn't exist.
    """
    r = run(["tmux", "display-message", "-p", "-t", str(target),
             "#{pane_current_command}"])
    return r.stdout.strip() if r.returncode == 0 else ""


def new_session(session: str, *, window: str = "manager",
                detached: bool = True, run: Callable = _default_run) -> bool:
    args = ["tmux", "new-session"] + (["-d"] if detached else []) + [
        "-s", session, "-n", window,
    ]
    return _ok(args, run)


def new_window(target: Target, *, run: Callable = _default_run) -> bool:
    return _ok(["tmux", "new-window", "-t", target.session, "-n", target.window], run)


def kill_window(target: Target, *, run: Callable = _default_run) -> bool:
    return _ok(["tmux", "kill-window", "-t", str(target)], run)


def kill_session(session: str, *, run: Callable = _default_run) -> bool:
    return _ok(["tmux", "kill-session", "-t", session], run)


def send_text(target: Target, text: str, *, run: Callable = _default_run) -> bool:
    """Send literal text (no key interpretation) to a pane.

    Uses `send-keys -l` so $/`/# don't get expanded by tmux.
    """
    return _ok(["tmux", "send-keys", "-l", "-t", str(target), text], run)


def send_keys(target: Target, *keys: str, run: Callable = _default_run) -> bool:
    """Send named keys (Enter, M-Enter, C-c, ...) to a pane."""
    return _ok(["tmux", "send-keys", "-t", str(target), *keys], run)


def inject(target: Target, text: str, *, submit_keys: list[str] | None = None,
           settle_ms: int = 200, sleep: Callable = time.sleep,
           run: Callable = _default_run) -> bool:
    """Send `text` into the pane and submit it with the PRIMARY submit key.

    Sends the literal text, settles, then sends `submit_keys[0]` only.
    Sending the whole fallback list (Enter / C-m / C-j …) every time used to
    leave trailing blank lines in a multi-line CLI's composer (codex) — that
    was both the "extra newline before the next message" and a source of
    unsubmitted-text pile-up. Verification + escalation through the rest of
    the key list live in `wake.inject_and_confirm`; this stays a single
    best-effort submit.

    Returns False if any subprocess call fails — callers can retry or
    surface the error.
    """
    if not send_text(target, text, run=run):
        return False
    sleep(settle_ms / 1000)
    key = (submit_keys or ["Enter"])[0]
    if not send_keys(target, key, run=run):
        return False
    sleep(settle_ms / 1000)
    return True


def spawn_agent(target: Target, spawn_cmd: str, *,
                run: Callable = _default_run) -> bool:
    """Drop a CLI spawn command into a pane and press Enter to start it."""
    if not send_text(target, spawn_cmd, run=run):
        return False
    return send_keys(target, "Enter", run=run)
