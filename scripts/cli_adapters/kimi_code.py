"""Kimi Code adapter — Step 2 补全,当前为骨架。

ready_markers / busy_markers 是推测值,需要 Step 2 实测采样后更新。
"""
from .base import CliAdapter


class KimiCodeAdapter(CliAdapter):
    def spawn_cmd(self, agent, model):
        return f"KIMI_AGENT={agent} kimi"

    def ready_markers(self):
        return ["kimi>", "Type your request"]

    def busy_markers(self):
        return [
            "⣾", "⣽", "⣻", "⢿", "⡿", "⣟", "⣯", "⣷",
            "Thinking",
        ]

    def process_name(self):
        return "kimi"
