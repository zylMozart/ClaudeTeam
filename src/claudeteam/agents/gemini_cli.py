"""Google Gemini CLI adapter.

Install: `npm install -g @google/gemini-cli`
Auth: OAuth (60/min + 1000/day free tier) / GEMINI_API_KEY / Vertex AI

Currently a thin shape match to the other adapters; spawn_cmd uses
`--approval-mode=yolo` (matches claude-code's `--dangerously-skip-permissions`
operationally — auto-approve every tool call so the agent runs unattended).

`model` is dropped because gemini-cli's model selection is via env or
config, not argv. Pass `GEMINI_MODEL` in the deployment environment if
the default doesn't fit.
"""
from __future__ import annotations

import shlex

from .base import CliAdapter, MULTILINE_SUBMIT_KEYS, SPINNER_CHARS


class GeminiCliAdapter(CliAdapter):
    def spawn_cmd(self, agent: str, model: str) -> str:
        # gemini-cli has no --name flag; tag agent identity via env so
        # /proc and log-parsers can correlate output with which pane.
        # DISABLE_UPDATE_CHECK avoids a blocking prompt at startup.
        return (
            f"DISABLE_UPDATE_CHECK=1 GEMINI_AGENT={shlex.quote(agent)} "
            f"gemini --approval-mode=yolo"
        )

    def ready_markers(self) -> list[str]:
        # TUI ready prompt; community gemini-cli wraps Ink so the trailing
        # `>` after the banner is the canonical marker. Add the tagline so
        # the spawn-cmd echo doesn't accidentally match.
        return ["Gemini>", "Gemini CLI"]

    def busy_markers(self) -> list[str]:
        return [
            *SPINNER_CHARS,
            "Thinking", "Running tool", "Calling",
        ]

    def process_name(self) -> str:
        return "gemini"

    def submit_keys(self) -> list[str]:
        # Ink-based UI, same submit pattern as Codex / Kimi
        return list(MULTILINE_SUBMIT_KEYS)

    def rate_limit_markers(self) -> list[str]:
        return [
            "rate limit",
            "quota exceeded",
            "RESOURCE_EXHAUSTED",
            "429",
        ]
