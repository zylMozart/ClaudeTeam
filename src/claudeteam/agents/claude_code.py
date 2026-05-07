"""Anthropic Claude Code adapter."""
from __future__ import annotations

import json
import os
import shlex
from pathlib import Path

from claudeteam.runtime import paths

from .base import CliAdapter, SPINNER_CHARS


def _read_oauth_token(agent: str) -> str | None:
    """Read the access token from the per-agent .credentials.json.

    Returns None if the file is missing or its shape doesn't match what
    claude writes. Best-effort: we'd rather spawn claude without the env
    var (and let it fall back to keychain) than crash the pane on a
    parse error.
    """
    cred = Path(agent_home(agent)) / ".claude" / ".credentials.json"
    if not cred.exists():
        return None
    try:
        data = json.loads(cred.read_text())
        token = data.get("claudeAiOauth", {}).get("accessToken")
        return token if isinstance(token, str) and token else None
    except (OSError, json.JSONDecodeError):
        return None


def agent_home(agent: str) -> str:
    """Per-agent HOME for an isolated ~/.claude.json.

    Container deploys (Dockerfile mounts /data writable) use
    /data/agent-home/<agent>. Host deploys (macOS firmlink read-only;
    Linux without that mount) fall back to <state_dir>/agent-home/<agent>.

    Probe writability rather than just existence: a Linux server might
    have /data as a read-only data disk mount where mkdir would fail,
    and macOS Big Sur+ has /data as a firmlink that exists() reports
    True for in some setups but rejects writes. Cache the probe result
    so we don't pay an os.access call per spawn.
    """
    if _data_writable():
        return f"/data/agent-home/{agent}"
    return str(paths.state_dir() / "agent-home" / agent)


_DATA_WRITABLE: bool | None = None


def _data_writable() -> bool:
    """Cached probe of whether /data/agent-home is a real writable dir."""
    global _DATA_WRITABLE
    if _DATA_WRITABLE is None:
        base = Path("/data/agent-home")
        try:
            base.mkdir(parents=True, exist_ok=True)
            _DATA_WRITABLE = os.access(base, os.W_OK)
        except OSError:
            _DATA_WRITABLE = False
    return _DATA_WRITABLE


class ClaudeCodeAdapter(CliAdapter):
    def spawn_cmd(self, agent: str, model: str) -> str:
        # Full silent-launch recipe — bypass-permissions confirm, theme picker, etc.
        # - IS_SANDBOX=1: claude allows --dangerously-skip-permissions
        # - CLAUDE_CODE_DISABLE_FEEDBACK_SURVEY=1 / DISABLE_AUTOUPDATER=1:
        #   silence survey + autoupdate banners.
        # - HOME=<agent_home>: per-agent home so each pane has its own
        #   ~/.claude.json — multiple panes sharing one config raced into
        #   "JSON Parse error" on restart. Lifecycle materialises the
        #   per-agent .claude/ from the live keychain (regular file, not
        #   symlink — claude's atomic-write replaces a symlink anyway).
        # - CLAUDE_CODE_OAUTH_TOKEN: hand claude the access token directly
        #   so it never asks the OS keychain. With per-agent HOME, claude's
        #   keychain *write* path on token refresh would otherwise pop the
        #   macOS "Keychain Not Found — Reset To Defaults" dialog (the
        #   storage keychain selection fails because the agent's HOME is
        #   off the user's login session). Pulling the token from
        #   ~/.claude/.credentials.json (lifecycle just refreshed it from
        #   keychain) and threading it through env keeps claude in
        #   file-only auth mode for the lifetime of the pane.
        oauth_token = _read_oauth_token(agent)
        token_prefix = (f"CLAUDE_CODE_OAUTH_TOKEN={shlex.quote(oauth_token)} "
                        if oauth_token else "")
        return (
            f"HOME={agent_home(agent)} "
            f"{token_prefix}"
            f"CLAUDE_CODE_DISABLE_FEEDBACK_SURVEY=1 DISABLE_AUTOUPDATER=1 "
            f"IS_SANDBOX=1 claude --dangerously-skip-permissions "
            f"--model {model} --name {agent}"
        )

    def ready_markers(self) -> list[str]:
        return ["bypass permissions on", "? for shortcuts"]

    def busy_markers(self) -> list[str]:
        return [
            *SPINNER_CHARS,
            "◐", "◑", "◒", "◓",
            "Thinking", "Running tool",
        ]

    def process_name(self) -> str:
        return "claude"

    def rate_limit_markers(self) -> list[str]:
        return [
            "Approaching usage limit",
            "5-hour limit reached",
            "Try again at",
            "rate limit",
        ]
