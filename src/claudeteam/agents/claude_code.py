"""Anthropic Claude Code adapter."""
from __future__ import annotations

from pathlib import Path

from claudeteam.runtime import paths

from .base import CliAdapter, SPINNER_CHARS


def agent_home(agent: str) -> str:
    """Per-agent HOME for an isolated ~/.claude.json.

    Container deploys (Dockerfile mounts /data) use /data/agent-home/<agent>.
    Host deploys (where /data is read-only or absent, e.g. macOS firmlink)
    fall back to <state_dir>/agent-home/<agent> so each pane still gets
    its own ~/.claude.json without needing root.
    """
    if Path("/data/agent-home").parent.exists():
        return f"/data/agent-home/{agent}"
    return str(paths.state_dir() / "agent-home" / agent)


class ClaudeCodeAdapter(CliAdapter):
    def spawn_cmd(self, agent: str, model: str) -> str:
        # R172.b: full silent-launch recipe (boss-provided 2026-05-04).
        # - IS_SANDBOX=1: claude allows --dangerously-skip-permissions
        # - CLAUDE_CODE_DISABLE_FEEDBACK_SURVEY=1 / DISABLE_AUTOUPDATER=1:
        #   silence survey + autoupdate banners.
        # - HOME=/data/agent-home/<agent>: per-agent home dir so each
        #   pane has isolated ~/.claude.json. Multiple panes sharing a
        #   single ~/.claude.json (the previous shape) hit concurrent-
        #   write corruption that popped a "JSON Parse error" dialog
        #   on the next restart. Each per-agent home has a symlink
        #   .claude/.credentials.json → /root/.claude/.credentials.json
        #   so OAuth tokens are still bind-mount shared (creds are
        #   read-mostly so the race risk is much smaller). Settings
        #   silent-launch flags also live in the per-agent home so
        #   the dialog skip persists.
        return (
            f"HOME={agent_home(agent)} "
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
