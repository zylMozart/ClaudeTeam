"""Codex CLI adapter (OpenAI, Apache-2.0).

安装: Docker 镜像构建期安装 @openai/codex；宿主机可用 brew/系统包管理器安装 codex
认证: ChatGPT Login / OPENAI_API_KEY

ready_markers / busy_markers 基于文档推测,标 TODO 待实测校准。
"""
import shlex

from .base import CliAdapter


class CodexCliAdapter(CliAdapter):
    # Codex 支持的 OpenAI 原生模型前缀; Claude 系列模型(opus/sonnet/haiku 等)
    # 不是 OpenAI 模型,传给 Codex 会报 400,尤其 ChatGPT 登录模式下。
    _OPENAI_MODEL_PREFIXES = ("gpt-", "o1", "o3", "o4", "codex")
    _REASONING_EFFORTS = {"low", "medium", "high", "xhigh"}

    def _reasoning_config(self, agent):
        # Codex CLI 0.121.0 consumes reasoning effort via -c
        # model_reasoning_effort="high". Keep "default"/"off" as no override.
        try:
            from claudeteam.runtime.config import resolve_thinking_for_agent
            thinking = resolve_thinking_for_agent(agent)
        except Exception:
            return []
        if thinking in self._REASONING_EFFORTS:
            return ["-c", f"model_reasoning_effort={thinking}"]
        return []

    def spawn_cmd(self, agent, model):
        args = ["--dangerously-bypass-approvals-and-sandbox"]
        # 只有 OpenAI 原生模型名才传 --model,其余(Claude 别名/全名)一律忽略,
        # 让 Codex CLI 自己选默认模型。
        if model and any(model.startswith(p) for p in self._OPENAI_MODEL_PREFIXES):
            args += ["--model", model]
        args += self._reasoning_config(agent)
        quoted_args = " ".join(shlex.quote(arg) for arg in args)
        quoted_agent = shlex.quote(agent)
        return (
            f"CODEX_AGENT={quoted_agent} "
            f"bash scripts/lib/run_codex_cli.sh {quoted_agent} {quoted_args}"
        )

    def ready_markers(self):
        # 实测 (codex-cli 0.124.0): TUI 就绪后 banner 显示 ">_ OpenAI Codex"
        # 和 "permissions: YOLO mode" 两行。用这两个做主 marker,避免误匹配
        # 冷启动时 tmux 回显的 spawn_cmd("--model gpt-5.4" 里含 "gpt-5")。
        # "tab to queue message" 仅在已有排队消息时出现,不是稳定 ready 信号。
        return ["OpenAI Codex", "permissions: YOLO"]

    def busy_markers(self):
        # 实测: codex 忙时 pane 出 "Working (Xs • esc to interrupt)",
        # 共用 "esc to interrupt" 足以覆盖。spinner 字符复用 CC 集合作为
        # 次要信号 (codex 0.121 的 spinner 还未实测,保守留 braille 全集)。
        return [
            "esc to interrupt",
            "⣾", "⣽", "⣻", "⢿", "⡿", "⣟", "⣯", "⣷",
        ]

    def process_name(self):
        return "codex"

    # resume_cmd: Codex CLI session 持久化待查, 暂返回 None (冷启动 fallback)
