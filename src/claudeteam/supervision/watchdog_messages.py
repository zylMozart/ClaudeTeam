"""Pure watchdog alert-message helpers."""
from __future__ import annotations


def build_burst_alert(proc_name: str) -> str:
    return f"[watchdog] {proc_name} 已崩溃并自动重启，请确认运行状态。"


def build_cooldown_alert(proc_name: str, max_retries: int, cooldown_secs: int) -> str:
    return (
        f"[watchdog] {proc_name} 连续 {max_retries} 次重启失败，已进入 "
        f"{cooldown_secs}s cooldown，期间 watchdog 不会重试。"
        f"cooldown 结束后自动重新尝试。"
    )
