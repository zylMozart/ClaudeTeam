"""Pure watchdog process-spec and env-gating helpers."""
from __future__ import annotations

from typing import Any, Iterable, Mapping


def env_enabled(name: str, *, env: Mapping[str, str] | None = None) -> bool:
    source = env if env is not None else {}
    return source.get(name, "").strip().lower() in ("1", "true", "yes", "on")


def build_lark_event_subscribe_cmd(lark_cli: Iterable[str]) -> str:
    return " ".join(lark_cli) + (
        " event +subscribe "
        "--event-types im.message.receive_v1 "
        "--compact --quiet --force --as bot"
    )


def build_process_specs(
    *,
    lark_cli: Iterable[str],
    router_pid_file: str,
    router_cursor_file: str,
    kanban_pid_file: str,
) -> list[dict[str, Any]]:
    lark_event_cmd = build_lark_event_subscribe_cmd(lark_cli)
    return [
        {
            "name": "router (lark-cli event | router)",
            "match": "feishu_router.py",
            "cmd": ["bash", "-c", f"{lark_event_cmd} | python3 scripts/feishu_router.py --stdin"],
            "pid_file": router_pid_file,
            "health_file": router_cursor_file,
            "health_stale_secs": 1800,
            "restart_grace_secs": 120,
            "max_retries": 3,
            "cooldown_secs": 600,
            "retry_count": 0,
            "last_restart_ts": 0,
            "cooldown_start_ts": 0,
        },
        {
            "name": "kanban_sync.py",
            "match": "kanban_sync.py daemon",
            "cmd": ["python3", "scripts/kanban_sync.py", "daemon"],
            "pid_file": kanban_pid_file,
            "max_retries": 3,
            "cooldown_secs": 600,
            "retry_count": 0,
            "cooldown_start_ts": 0,
        },
    ]


def filter_enabled_processes(
    procs: Iterable[dict[str, Any]],
    *,
    env: Mapping[str, str] | None = None,
) -> list[dict[str, Any]]:
    source = env if env is not None else {}
    enabled = []
    for proc in procs:
        match = str(proc.get("match", ""))
        if "feishu_router.py" in match and not env_enabled("CLAUDETEAM_ENABLE_FEISHU_REMOTE", env=source):
            continue
        if "kanban_sync.py" in match and not env_enabled("CLAUDETEAM_ENABLE_BITABLE_LEGACY", env=source):
            continue
        enabled.append(dict(proc))
    return enabled

