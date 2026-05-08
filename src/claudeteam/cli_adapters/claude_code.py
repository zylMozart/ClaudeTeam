"""Claude Code adapter — 封装当前所有 CC 硬编码的启动/探测/忙碌逻辑。"""
from .base import CliAdapter
from claudeteam.runtime.config import resolve_proxy_config


class ClaudeCodeAdapter(CliAdapter):
    def _proxy_prefix(self, agent, model):
        api_base, api_key = resolve_proxy_config(agent)
        if not api_base:
            return ""
        parts = [f"ANTHROPIC_BASE_URL={api_base}"]
        if api_key:
            parts.append(f"ANTHROPIC_API_KEY={api_key}")
        parts.append(f"ANTHROPIC_MODEL={model}")
        return " ".join(parts) + " "

    def spawn_cmd(self, agent, model):
        prefix = self._proxy_prefix(agent, model)
        return (f"{prefix}IS_SANDBOX=1 claude --dangerously-skip-permissions"
                f" --model {model} --name {agent}")

    def ready_markers(self):
        return ["bypass permissions on", "? for shortcuts"]

    def busy_markers(self):
        return [
            "⣾", "⣽", "⣻", "⢿", "⡿", "⣟", "⣯", "⣷",
            "◐", "◑", "◒", "◓",
            "Thinking", "Running tool",
        ]

    def process_name(self):
        return "claude"

    def resume_cmd(self, agent, model, sid):
        prefix = self._proxy_prefix(agent, model)
        return (f"{prefix}IS_SANDBOX=1 claude --dangerously-skip-permissions"
                f" --model {model} --name {agent} --resume {sid}")

    def thinking_init_hint(self, thinking):
        return {
            "high": "Use extended thinking for complex tasks.",
            "low": "Keep thinking concise.",
            "off": "Do not use extended thinking.",
        }.get(thinking)  # "default" → None
