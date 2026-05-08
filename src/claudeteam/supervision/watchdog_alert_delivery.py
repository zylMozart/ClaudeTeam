"""Pure helpers for watchdog alert-delivery result handling."""
from __future__ import annotations


def summarize_alert_send_failure(
    stdout: str | None,
    stderr: str | None,
    limit: int = 300,
) -> str:
    return (stderr or "").strip()[:limit] or (stdout or "").strip()[:limit] or "(无输出)"


def build_alert_delivery_log_line(
    returncode: int,
    log_label: str,
    stdout: str | None,
    stderr: str | None,
) -> str:
    if returncode == 0:
        return f"📨 已通知 manager: {log_label}"
    if returncode == 2:
        return f"⚠️ 已通知 manager(收件箱OK,群通知失败): {log_label}"
    err = summarize_alert_send_failure(stdout, stderr, limit=300)
    return f"🚨 通知 manager 失败 (exit={returncode}): {log_label} — {err}"
