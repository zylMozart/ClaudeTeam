"""Codex CLI adapter (OpenAI, Apache-2.0).

安装: npm install -g @openai/codex  或  brew install codex
认证: ChatGPT Login / OPENAI_API_KEY

ready_markers / busy_markers 基于文档推测,标 TODO 待实测校准。
"""
from .base import CliAdapter


class CodexCliAdapter(CliAdapter):
    def spawn_cmd(self, agent, model):
        if model:
            return f"CODEX_AGENT={agent} codex --full-auto --model {model}"
        return f"CODEX_AGENT={agent} codex --full-auto"

    def ready_markers(self):
        # TODO: 实测校准 — Codex CLI TUI 的 ready 特征串
        return ["codex>", ">"]

    def busy_markers(self):
        # TODO: 实测校准
        return [
            "⣾", "⣽", "⣻", "⢿", "⡿", "⣟", "⣯", "⣷",  # braille spinner
            "Thinking", "Running tool",
        ]

    def process_name(self):
        return "codex"

    # resume_cmd: Codex CLI session 持久化待查, 暂返回 None (冷启动 fallback)
