"""CLI adapter 注册表 — 按 team.json 的 cli 字段分发。"""
import json
import os

from .base import CliAdapter
from .claude_code import ClaudeCodeAdapter
from .kimi_code import KimiCodeAdapter
from .gemini_cli import GeminiCliAdapter
from .codex_cli import CodexCliAdapter
from .qwen_code import QwenCodeAdapter

_REGISTRY: dict[str, CliAdapter] = {
    "claude-code": ClaudeCodeAdapter(),
    "kimi-code": KimiCodeAdapter(),
    "gemini-cli": GeminiCliAdapter(),
    "codex-cli": CodexCliAdapter(),
    "qwen-code": QwenCodeAdapter(),
}


def get_adapter(cli_name: str) -> CliAdapter:
    if cli_name not in _REGISTRY:
        raise ValueError(
            f"Unknown CLI adapter: {cli_name!r}. "
            f"Available: {', '.join(_REGISTRY)}"
        )
    return _REGISTRY[cli_name]


def adapter_for_agent(agent_name: str) -> CliAdapter:
    """读 team.json 的 cli 字段,缺省 'claude-code'。"""
    team_file = os.path.join(
        os.path.dirname(__file__), "..", "..", "team.json"
    )
    try:
        with open(team_file) as f:
            team = json.load(f)
        cli = (team.get("agents", {})
               .get(agent_name, {})
               .get("cli", "claude-code"))
    except (FileNotFoundError, json.JSONDecodeError, AttributeError):
        cli = "claude-code"
    return get_adapter(cli)
