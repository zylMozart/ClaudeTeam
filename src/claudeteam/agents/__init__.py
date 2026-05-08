"""CLI adapter registry — maps a `cli` identifier to its CliAdapter."""
from __future__ import annotations

from claudeteam.runtime.config import agent_cli

from .base import CliAdapter
from .claude_code import ClaudeCodeAdapter
from .codex_cli import CodexCliAdapter
from .gemini_cli import GeminiCliAdapter
from .kimi_code import KimiCodeAdapter
from .qwen_code import QwenCodeAdapter


_kimi = KimiCodeAdapter()
_qwen = QwenCodeAdapter()
_REGISTRY: dict[str, CliAdapter] = {
    "claude-code": ClaudeCodeAdapter(),
    "codex-cli": CodexCliAdapter(),
    "gemini-cli": GeminiCliAdapter(),
    "kimi-code": _kimi,
    "kimi-cli": _kimi,  # alias: upstream package name
    "qwen-code": _qwen,
    "qwen-cli": _qwen,  # alias for symmetry with kimi
}


def known_clis() -> tuple[str, ...]:
    return tuple(_REGISTRY)


def get_adapter(cli_name: str) -> CliAdapter:
    """Return the adapter for `cli_name`. Raises KeyError if not registered."""
    if cli_name not in _REGISTRY:
        raise KeyError(
            f"unknown cli: {cli_name!r} (known: {', '.join(_REGISTRY)})")
    return _REGISTRY[cli_name]


def adapter_for_agent(agent: str) -> CliAdapter:
    """Look up the agent's `cli` from team.json and return its adapter.

    Convenience over `get_adapter(config.agent_cli(agent))`; the routing
    layer reaches for this whenever it needs to spawn or inspect a pane.
    """
    return get_adapter(agent_cli(agent))
