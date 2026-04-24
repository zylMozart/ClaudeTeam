"""Pure health-check decision helpers for watchdog."""
from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class HealthCheckDecision:
    skip_health_file_check: bool
    health_file_stale: bool


def should_skip_health_file_check(
    *,
    now: float,
    last_restart_ts: float,
    restart_grace_secs: float,
) -> bool:
    if restart_grace_secs <= 0:
        return False
    return (now - last_restart_ts) < restart_grace_secs


def is_health_file_stale(*, age_secs: float, health_stale_secs: float) -> bool:
    return age_secs > health_stale_secs


def decide_health_file_state(
    *,
    now: float,
    last_restart_ts: float,
    restart_grace_secs: float,
    health_file_age_secs: float | None,
    health_stale_secs: float,
) -> HealthCheckDecision:
    skip = should_skip_health_file_check(
        now=now,
        last_restart_ts=last_restart_ts,
        restart_grace_secs=restart_grace_secs,
    )
    if skip:
        return HealthCheckDecision(
            skip_health_file_check=True,
            health_file_stale=False,
        )
    if health_file_age_secs is None:
        return HealthCheckDecision(
            skip_health_file_check=False,
            health_file_stale=False,
        )
    return HealthCheckDecision(
        skip_health_file_check=False,
        health_file_stale=is_health_file_stale(
            age_secs=health_file_age_secs,
            health_stale_secs=health_stale_secs,
        ),
    )
