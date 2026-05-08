"""Gemini CLI adapter (Google, Apache-2.0).

安装: npm install -g @google/gemini-cli
认证: OAuth (60/min + 1000/day 免费) / GEMINI_API_KEY / Vertex AI

ready_markers / busy_markers 基于文档推测,标 TODO 待实测校准。
"""
from .base import CliAdapter


class GeminiCliAdapter(CliAdapter):
    def spawn_cmd(self, agent, model):
        # Gemini CLI 无 --name; 用 env 标注 agent 身份供日志识别
        return f"DISABLE_UPDATE_CHECK=1 GEMINI_AGENT={agent} gemini --approval-mode=yolo"

    def ready_markers(self):
        # TODO: 实测校准 — Gemini CLI TUI 的 ready 特征串
        return ["Gemini>", ">"]

    def busy_markers(self):
        # TODO: 实测校准
        return [
            "⣾", "⣽", "⣻", "⢿", "⡿", "⣟", "⣯", "⣷",  # braille spinner
            "Thinking", "Running",
        ]

    def process_name(self):
        return "gemini"

    # resume_cmd: Gemini CLI 有 "checkpointing" 机制但 CLI 旗标待核,
    # 暂返回 None (冷启动 fallback)
