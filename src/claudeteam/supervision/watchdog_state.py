"""Pure state transition helpers for watchdog check loop."""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Mapping


ACTION_HEALTHY = "healthy"
ACTION_HEALTHY_RESET = "healthy_reset"
ACTION_COOLDOWN_WAIT = "cooldown_wait"
ACTION_RESTART = "restart"
ACTION_ENTER_COOLDOWN = "enter_cooldown"


@dataclass(frozen=True)
class WatchdogStateDecision:
    action: str
    retry_count: int
    cooldown_start_ts: float
    max_retries: int
    cooldown_secs: int
    cooldown_remaining_secs: int = 0
    cooldown_ended: bool = False


def decide_watchdog_state(
    proc: Mapping[str, Any],
    *,
    healthy: bool,
    now: float,
) -> WatchdogStateDecision:
    retry_count = int(proc.get("retry_count", 0) or 0)
    cooldown_start_ts = float(proc.get("cooldown_start_ts", 0) or 0)
    max_retries = int(proc.get("max_retries", 3) or 3)
    cooldown_secs = int(proc.get("cooldown_secs", 600) or 600)

    if healthy:
        if retry_count > 0 or cooldown_start_ts > 0:
            return WatchdogStateDecision(
                action=ACTION_HEALTHY_RESET,
                retry_count=0,
                cooldown_start_ts=0.0,
                max_retries=max_retries,
                cooldown_secs=cooldown_secs,
            )
        return WatchdogStateDecision(
            action=ACTION_HEALTHY,
            retry_count=retry_count,
            cooldown_start_ts=cooldown_start_ts,
            max_retries=max_retries,
            cooldown_secs=cooldown_secs,
        )

    cooldown_ended = False
    if cooldown_start_ts > 0:
        elapsed = now - cooldown_start_ts
        if elapsed < cooldown_secs:
            return WatchdogStateDecision(
                action=ACTION_COOLDOWN_WAIT,
                retry_count=retry_count,
                cooldown_start_ts=cooldown_start_ts,
                max_retries=max_retries,
                cooldown_secs=cooldown_secs,
                cooldown_remaining_secs=int(cooldown_secs - elapsed),
            )
        cooldown_ended = True
        retry_count = 0
        cooldown_start_ts = 0.0

    retry_count += 1
    if retry_count > max_retries:
        return WatchdogStateDecision(
            action=ACTION_ENTER_COOLDOWN,
            retry_count=retry_count,
            cooldown_start_ts=now,
            max_retries=max_retries,
            cooldown_secs=cooldown_secs,
            cooldown_ended=cooldown_ended,
        )

    return WatchdogStateDecision(
        action=ACTION_RESTART,
        retry_count=retry_count,
        cooldown_start_ts=cooldown_start_ts,
        max_retries=max_retries,
        cooldown_secs=cooldown_secs,
        cooldown_ended=cooldown_ended,
    )

