"""CLI Adapter ABC — 每种 CLI 工具实现这个接口。"""
from abc import ABC, abstractmethod


class CliAdapter(ABC):
    @abstractmethod
    def spawn_cmd(self, agent: str, model: str) -> str:
        """tmux send-keys 用的完整启动命令字符串。"""

    @abstractmethod
    def ready_markers(self) -> list:
        """pane 内出现任一 → CLI UI 已就绪。"""

    @abstractmethod
    def busy_markers(self) -> list:
        """pane 末尾出现任一 → agent 正忙。
        since 2026-04-25 only used by quick_idle_hint（is_agent_idle 已切到 pane-diff）。
        """

    @abstractmethod
    def process_name(self) -> str:
        """/proc/<pid>/comm 里期望的进程名。

        Legacy single-name interface. Detector callers should prefer
        :meth:`process_names` so wrapper processes (``node`` etc.) match too.
        Removed once the stage 2 grayscale window
        (``CLAUDETEAM_DETECTOR_LEGACY``) closes.
        """

    def process_names(self) -> set:
        """Set of acceptable ``pane_current_command`` values for this CLI.

        Stage 2 detector (``claudeteam.runtime.agent_detector``) treats any of
        these as evidence the CLI is running. Default = ``{process_name()}``;
        subclasses override to add wrapper names like ``node``, ``python3``
        etc. Wrappers are common — Node-based CLIs (claude / codex / gemini /
        qwen) often present ``node`` as ``pane_current_command`` instead of
        their own binary name.
        """
        return {self.process_name()}

    def resume_cmd(self, agent: str, model: str, sid: str):
        """session 恢复命令。返回 None 表示不支持 resume。"""
        return None

    def env_overrides(self, agent: str) -> dict:
        """adapter 级别的额外环境变量。"""
        return {}

    def thinking_init_hint(self, thinking: str):
        """根据 thinking level 返回 init 消息追加的 hint,或 None。"""
        return None

    def submit_keys(self) -> list:
        """tmux 提交当前输入时按顺序尝试的按键。"""
        return ["Enter", "C-m", "C-j"]
