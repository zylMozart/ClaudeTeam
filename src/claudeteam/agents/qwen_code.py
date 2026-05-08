"""Alibaba Qwen Code CLI adapter.

Install: `npm install -g qwen-code` or `brew install qwen-code`
Auth: OAuth (qwen.ai) / API key (阿里云 Coding Plan / OpenAI-compat / Anthropic-compat)

Same shape as gemini-cli: Ink-based TUI, --yolo for auto-approve,
agent identity tagged via env. Model selection is via env / config, not
argv — drop the `model` parameter so a stale alias doesn't reach qwen.
"""
from __future__ import annotations

import shlex

from .base import CliAdapter, MULTILINE_SUBMIT_KEYS, SPINNER_CHARS


class QwenCodeAdapter(CliAdapter):
    def spawn_cmd(self, agent: str, model: str) -> str:
        # qwen has no --name flag; tag agent via env so log scrapers can
        # correlate output with which pane. DISABLE_UPDATE_CHECK avoids
        # a blocking "update available" prompt at startup. --yolo
        # auto-approves every tool call (matches claude-code's
        # `--dangerously-skip-permissions` operationally — required for
        # unattended pane operation).
        return (
            f"DISABLE_UPDATE_CHECK=1 QWEN_AGENT_NAME={shlex.quote(agent)} "
            f"qwen --yolo"
        )

    def ready_markers(self) -> list[str]:
        # TUI ready markers from qwen-code's Ink banner + prompt; "qwen>"
        # is the canonical input cursor. "Type your request" appears in
        # the welcome banner when first launched.
        return ["qwen>", "Type your request", "Qwen Code"]

    def busy_markers(self) -> list[str]:
        return [
            *SPINNER_CHARS,
            "Thinking", "Calling tool", "Running",
        ]

    def process_name(self) -> str:
        return "qwen"

    def submit_keys(self) -> list[str]:
        # Ink-based UI, same submit pattern as Codex / Kimi / Gemini
        return list(MULTILINE_SUBMIT_KEYS)

    def rate_limit_markers(self) -> list[str]:
        return [
            "rate limit",
            "quota exceeded",
            "请求过于频繁",  # Chinese-localised throttle message
            "429",
        ]
