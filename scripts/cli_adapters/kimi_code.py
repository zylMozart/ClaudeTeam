"""Kimi Code adapter (Moonshot AI, technical preview).

安装: uv tool install kimi-cli
认证: Moonshot 账号登录
平台: macOS / Linux

ready_markers / busy_markers 基于文档推测,标 TODO 待实测校准。
"""
from .base import CliAdapter


class KimiCodeAdapter(CliAdapter):
    def spawn_cmd(self, agent, model):
        return f"KIMI_AGENT={agent} kimi --yolo"

    def ready_markers(self):
        return [
            "Welcome to Kimi Code CLI",
            "Send /help for help information",
            "── input",
            "context:",
        ]

    def busy_markers(self):
        # TODO: 实测校准
        return [
            "⣾", "⣽", "⣻", "⢿", "⡿", "⣟", "⣯", "⣷",  # braille spinner
            "Thinking",
        ]

    def process_name(self):
        return "kimi"

    # resume_cmd: Kimi CLI 无公开 --resume 机制, 返回 None (冷启动 fallback)
