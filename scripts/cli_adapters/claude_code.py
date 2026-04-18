"""Claude Code adapter — 封装当前所有 CC 硬编码的启动/探测/忙碌逻辑。"""
from .base import CliAdapter


class ClaudeCodeAdapter(CliAdapter):
    def spawn_cmd(self, agent, model):
        return (f"IS_SANDBOX=1 claude --dangerously-skip-permissions"
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
        return (f"IS_SANDBOX=1 claude --dangerously-skip-permissions"
                f" --model {model} --name {agent} --resume {sid}")
