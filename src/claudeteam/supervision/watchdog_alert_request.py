"""Pure helpers for watchdog alert command and label/message shaping."""
from __future__ import annotations


def normalize_alert_message(message: str) -> str:
    return message


def normalize_alert_log_label(log_label: str) -> str:
    return log_label


def build_manager_alert_send_cmd(message: str) -> list[str]:
    return [
        "python3",
        "scripts/feishu_msg.py",
        "send",
        "manager",
        "watchdog",
        message,
        "高",
    ]


def build_testing_skip_log_line(log_label: str, message: str, preview_limit: int = 120) -> str:
    return f"🧪 [TESTING] 已跳过真实 manager 告警: {log_label} — {message[:preview_limit]}"
