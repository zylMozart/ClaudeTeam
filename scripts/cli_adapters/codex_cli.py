"""Codex CLI adapter (OpenAI, Apache-2.0).

安装: npm install -g @openai/codex  或  brew install codex
认证: ChatGPT Login / OPENAI_API_KEY

ready_markers / busy_markers 基于文档推测,标 TODO 待实测校准。
"""
from .base import CliAdapter


class CodexCliAdapter(CliAdapter):
    # Codex 支持的 OpenAI 原生模型前缀; Claude 系列模型(opus/sonnet/haiku 等)
    # 不是 OpenAI 模型,传给 Codex 会报 400,尤其 ChatGPT 登录模式下。
    _OPENAI_MODEL_PREFIXES = ("gpt-", "o1", "o3", "o4", "codex")

    def spawn_cmd(self, agent, model):
        # 只有 OpenAI 原生模型名才传 --model,其余(Claude 别名/全名)一律忽略,
        # 让 Codex CLI 自己选默认模型。
        if model and any(model.startswith(p) for p in self._OPENAI_MODEL_PREFIXES):
            return f"CODEX_AGENT={agent} codex --dangerously-bypass-approvals-and-sandbox --model {model}"
        return f"CODEX_AGENT={agent} codex --dangerously-bypass-approvals-and-sandbox"

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
