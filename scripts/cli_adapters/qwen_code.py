"""Qwen Code adapter (Alibaba, Apache-2.0).

安装: npm install -g qwen-code  或  brew install qwen-code
认证: OAuth (qwen.ai) / API key (阿里云 Coding Plan / OpenAI-compat / Anthropic-compat)

ready_markers / busy_markers 基于文档推测,标 TODO 待实测校准。
"""
from .base import CliAdapter


class QwenCodeAdapter(CliAdapter):
    def spawn_cmd(self, agent, model):
        # Qwen Code 无 --name; 用 env 标注 agent 身份供日志识别
        # --yolo 跳过权限确认 (类似 CC 的 --dangerously-skip-permissions)
        return f"QWEN_AGENT_NAME={agent} qwen --yolo"

    def ready_markers(self):
        # TODO: 实测校准 — Qwen Code TUI 的 ready 特征串
        return ["qwen>", ">", "Type your request"]

    def busy_markers(self):
        # TODO: 实测校准
        return [
            "⣾", "⣽", "⣻", "⢿", "⡿", "⣟", "⣯", "⣷",  # braille spinner
            "Thinking",
        ]

    def process_name(self):
        return "qwen"

    # resume_cmd: Qwen Code 无公开 --resume, 返回 None (冷启动 fallback)
