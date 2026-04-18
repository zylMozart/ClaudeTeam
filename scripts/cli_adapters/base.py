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
        """pane 末尾出现任一 → agent 正忙。"""

    @abstractmethod
    def process_name(self) -> str:
        """/proc/<pid>/comm 里期望的进程名。"""

    def resume_cmd(self, agent: str, model: str, sid: str):
        """session 恢复命令。返回 None 表示不支持 resume。"""
        return None

    def env_overrides(self, agent: str) -> dict:
        """adapter 级别的额外环境变量。"""
        return {}
