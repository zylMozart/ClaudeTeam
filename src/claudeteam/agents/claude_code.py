"""Anthropic Claude Code adapter."""
from __future__ import annotations

from .base import CliAdapter, SPINNER_CHARS


class ClaudeCodeAdapter(CliAdapter):
    def spawn_cmd(self, agent: str, model: str) -> str:
        # R172.b: full silent-launch recipe (boss-provided 2026-05-04).
        # IS_SANDBOX=1 → claude allows --dangerously-skip-permissions
        #                 (otherwise refuses with "must run as non-root
        #                 in sandbox container").
        # CLAUDE_CODE_DISABLE_FEEDBACK_SURVEY=1 → suppresses the periodic
        #                 satisfaction-survey toast that pops over the
        #                 prompt (was triggering tmux capture-pane to
        #                 mis-read pane state).
        # DISABLE_AUTOUPDATER=1 → suppresses the "claude X.Y available"
        #                 banner that flashes after `claude --version`
        #                 on first launch in fresh containers.
        # Combined with global `skipDangerousModePermissionPrompt: true`
        # in /root/.claude/settings.json (written at container build),
        # the pane should show "bypass permissions on" within ~2s of
        # spawn — no dialog, no toast, no survey.
        return (
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
