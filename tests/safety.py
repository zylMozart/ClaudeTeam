"""Reusable no-live safety guards for tests.

This module intentionally has no pytest dependency so it can be checked with
plain Python on hosts where pytest is not installed.
"""
from __future__ import annotations

import os
import shlex
from pathlib import Path
from typing import Iterable


PROJECT_ROOT = Path(__file__).resolve().parents[1]
LIVE_ENV_VAR = "CLAUDETEAM_LIVE_TESTS"

DANGEROUS_COMMANDS = frozenset({
    "docker",
    "docker-compose",
    "lark-cli",
    "tmux",
})

DANGEROUS_COMMAND_ARGS = frozenset({
    "@larksuite/cli",
    "feishu_msg.py",
    "feishu_router.py",
})

DEFAULT_ALLOWED_LOCAL_COMMANDS = frozenset({
    "bash",
    "cat",
    "echo",
    "false",
    "printf",
    "python",
    "python3",
    "sh",
    "true",
})


class ExternalCallBlocked(AssertionError):
    """Raised when a non-live test attempts a real external boundary."""


class LiveTestNotConfirmed(AssertionError):
    """Raised when live/smoke tests are selected without env confirmation."""


def live_tests_confirmed(environ: dict[str, str] | None = None) -> bool:
    env = os.environ if environ is None else environ
    return env.get(LIVE_ENV_VAR) == "1"


def require_live_confirmation(environ: dict[str, str] | None = None) -> None:
    if not live_tests_confirmed(environ):
        raise LiveTestNotConfirmed(
            f"Live/smoke tests require {LIVE_ENV_VAR}=1. "
            "Default unit/contract runs must stay no-live."
        )


def command_tokens(cmd) -> list[str]:
    if isinstance(cmd, (list, tuple)):
        return [str(part) for part in cmd]
    if isinstance(cmd, str):
        try:
            return shlex.split(cmd)
        except ValueError:
            return [cmd]
    return [str(cmd)]


def command_display(cmd) -> str:
    tokens = command_tokens(cmd)
    return " ".join(tokens) if tokens else "<missing command>"


def command_basename(token: str) -> str:
    return Path(token).name


def is_dangerous_command(cmd) -> bool:
    tokens = command_tokens(cmd)
    if not tokens:
        return True
    if command_basename(tokens[0]) in DANGEROUS_COMMANDS:
        return True
    for part in tokens[1:]:
        base = command_basename(part)
        if base in DANGEROUS_COMMAND_ARGS or part in DANGEROUS_COMMAND_ARGS:
            return True
        # Shell payloads such as ["bash", "-lc", "tmux ls"] should not bypass
        # the allowlist merely because the executable is local.
        for dangerous in DANGEROUS_COMMANDS | DANGEROUS_COMMAND_ARGS:
            if dangerous in part:
                return True
    return False


def allowed_local_commands(marker_args: Iterable[str] = ()) -> set[str]:
    requested = {str(item) for item in marker_args if str(item).strip()}
    if not requested:
        return set(DEFAULT_ALLOWED_LOCAL_COMMANDS)
    return {command_basename(item) for item in requested}


def assert_subprocess_allowed(cmd, *, marker_args: Iterable[str] = ()) -> None:
    tokens = command_tokens(cmd)
    executable = command_basename(tokens[0]) if tokens else ""
    allowed = allowed_local_commands(marker_args)
    if is_dangerous_command(cmd):
        raise ExternalCallBlocked(
            "Blocked dangerous external command even with allow_subprocess: "
            f"{command_display(cmd)!r}. Use live/smoke with "
            f"{LIVE_ENV_VAR}=1 for real tmux/docker/Feishu boundaries."
        )
    if executable not in allowed:
        raise ExternalCallBlocked(
            "Blocked subprocess command outside allow_subprocess local allowlist: "
            f"{command_display(cmd)!r}. Allowed executables: {sorted(allowed)!r}."
        )


def blocked_subprocess_message(cmd) -> str:
    return (
        "Blocked external subprocess call in non-live pytest run: "
        f"{command_display(cmd)!r}. Monkeypatch the call site, or mark the test "
        f"live/smoke and run with {LIVE_ENV_VAR}=1."
    )


def blocked_network_message(target: str) -> str:
    return (
        "Blocked external network/socket call in non-live pytest run: "
        f"{target}. Monkeypatch the call site, or mark the test live/smoke "
        f"and run with {LIVE_ENV_VAR}=1."
    )
