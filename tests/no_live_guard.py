#!/usr/bin/env python3
"""No-live guard for default ClaudeTeam tests.

Default tests must be runnable on a developer laptop or in CI without touching
real Feishu, tmux, Docker, network, or credential-backed tools.  The guard is
installed by tests/run_no_live.py before any test module is executed.
"""
from __future__ import annotations

import os
import socket
import subprocess
import urllib.request
from collections.abc import Sequence


_ORIGINALS = {
    "subprocess_run": subprocess.run,
    "subprocess_popen": subprocess.Popen,
    "subprocess_call": subprocess.call,
    "subprocess_check_call": subprocess.check_call,
    "subprocess_check_output": subprocess.check_output,
    "socket_create_connection": socket.create_connection,
    "urllib_urlopen": urllib.request.urlopen,
}

_INSTALLED = False


LIVE_TOOL_TOKENS = (
    "npx",
    "@larksuite/cli",
    "lark-cli",
    "tmux",
    "docker",
    "sudo",
    "curl",
    "wget",
    "ssh",
    "scp",
)

CREDENTIAL_ENV_PREFIXES = (
    "FEISHU_",
    "LARK_",
    "ANTHROPIC_",
    "OPENAI_",
    "GEMINI_",
    "KIMI_",
    "QWEN_",
)


class NoLiveAccessError(RuntimeError):
    """Raised when a default test tries to touch live infrastructure."""


def _flatten_cmd(cmd) -> list[str]:
    if isinstance(cmd, str):
        return cmd.split()
    if isinstance(cmd, Sequence):
        return [str(part) for part in cmd]
    return [str(cmd)]


def _looks_live_command(cmd) -> bool:
    parts = _flatten_cmd(cmd)
    joined = " ".join(parts)
    if "@larksuite/cli" in joined or "lark-cli" in joined:
        return True
    if "agent_lifecycle.sh" in joined:
        return True
    first = os.path.basename(parts[0]) if parts else ""
    return first in LIVE_TOOL_TOKENS


def _blocked_subprocess(*args, **kwargs):
    cmd = args[0] if args else kwargs.get("args", "")
    if _looks_live_command(cmd):
        raise NoLiveAccessError(f"blocked live subprocess in no-live tests: {cmd!r}")
    return _ORIGINALS["subprocess_run"](*args, **kwargs)


class _BlockedPopen(subprocess.Popen):
    def __init__(self, args, *popen_args, **popen_kwargs):
        if _looks_live_command(args):
            raise NoLiveAccessError(
                f"blocked live subprocess in no-live tests: {args!r}"
            )
        super().__init__(args, *popen_args, **popen_kwargs)


def _blocked_socket(*args, **kwargs):
    raise NoLiveAccessError("blocked network socket in no-live tests")


def _blocked_urlopen(*args, **kwargs):
    raise NoLiveAccessError("blocked urllib network access in no-live tests")


def _scrub_credential_env() -> None:
    for key in list(os.environ):
        if key.startswith(CREDENTIAL_ENV_PREFIXES):
            os.environ.pop(key, None)


def install() -> None:
    """Install the process-wide no-live guard once."""
    global _INSTALLED
    if _INSTALLED:
        return
    _scrub_credential_env()
    subprocess.run = _blocked_subprocess
    subprocess.Popen = _BlockedPopen
    subprocess.call = lambda *a, **kw: _blocked_subprocess(*a, **kw).returncode
    subprocess.check_call = lambda *a, **kw: _raise_on_nonzero(
        _blocked_subprocess(*a, **kw)
    )
    subprocess.check_output = lambda *a, **kw: _blocked_subprocess(
        *a, capture_output=True, **kw
    ).stdout
    socket.create_connection = _blocked_socket
    urllib.request.urlopen = _blocked_urlopen
    _INSTALLED = True


def _raise_on_nonzero(result):
    if result.returncode != 0:
        raise subprocess.CalledProcessError(result.returncode, result.args)
    return 0
