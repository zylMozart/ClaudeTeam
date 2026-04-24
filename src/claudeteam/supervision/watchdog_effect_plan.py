"""Pure decision->effect-plan helpers for watchdog check loop."""
from __future__ import annotations

from dataclasses import dataclass


EFFECT_CONTINUE = "continue"
EFFECT_ALERT_ONLY = "alert_only"
EFFECT_RESTART_NOTIFY = "restart_notify"


@dataclass(frozen=True)
class WatchdogEffectPlan:
    mark_unhealthy: bool
    effect: str
    log_lines: tuple[str, ...] = ()


def build_effect_plan(
    *,
    proc_name: str,
    action: str,
    retry_count: int,
    cooldown_remaining_secs: int = 0,
    cooldown_ended: bool = False,
    max_retries: int = 0,
    cooldown_secs: int = 0,
    action_healthy: str,
    action_healthy_reset: str,
    action_cooldown_wait: str,
    action_enter_cooldown: str,
) -> WatchdogEffectPlan:
    if action == action_healthy:
        return WatchdogEffectPlan(mark_unhealthy=False, effect=EFFECT_CONTINUE)

    if action == action_healthy_reset:
        return WatchdogEffectPlan(
            mark_unhealthy=False,
            effect=EFFECT_CONTINUE,
            log_lines=(f"✅ {proc_name} 恢复健康，重置重试计数",),
        )

    if action == action_cooldown_wait:
        return WatchdogEffectPlan(
            mark_unhealthy=True,
            effect=EFFECT_CONTINUE,
            log_lines=(f"⏸  {proc_name} 仍在 cooldown (剩余 {cooldown_remaining_secs}s)，跳过本轮",),
        )

    logs: list[str] = []
    if cooldown_ended:
        logs.append(f"🔁 {proc_name} cooldown 结束 ({cooldown_secs}s)，重新开始重启 burst")

    if action == action_enter_cooldown:
        logs.append(f"🚨 {proc_name} 连续 {max_retries} 次重启失败，进入 cooldown ({cooldown_secs}s)")
        return WatchdogEffectPlan(
            mark_unhealthy=True,
            effect=EFFECT_ALERT_ONLY,
            log_lines=tuple(logs),
        )

    logs.append(f"💀 检测到异常: {proc_name} (第 {retry_count} 次)")
    return WatchdogEffectPlan(
        mark_unhealthy=True,
        effect=EFFECT_RESTART_NOTIFY,
        log_lines=tuple(logs),
    )
