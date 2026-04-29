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
    router_tmux_target: str = "",
) -> list[dict[str, Any]]:
    lark_event_cmd = build_lark_event_subscribe_cmd(lark_cli)
    return [
        {
            "name": "router (lark-cli event | router)",
            "match": "feishu_router.py",
            "cmd": ["bash", "-c", f"{lark_event_cmd} | python3 scripts/feishu_router.py --stdin"],
            "pid_file": router_pid_file,
            "health_file": router_cursor_file,
            # router daemon 自带 30s/拍 独立心跳线程 (router_autoheal_design §2.1),
            # 90s = 3 拍漏判即视为不健康. 旧 180s 上限对配合 events-only 心跳的旧
            # 路径合理, 加上心跳线程后冗余太多, 故障窗口缩到原来的一半.
            # 60s grace 给冷启动: heartbeat thread 第一次 touch 之前 router 自己
            # 在 main() 启动末尾会 _refresh_heartbeat() 一次, 之后心跳线程接班,
            # 60s 留充足余量给冷 npm / Python import.
            "health_stale_secs": 90,
            "restart_grace_secs": 60,
            "max_retries": 3,
            "cooldown_secs": 600,
            "retry_count": 0,
            "last_restart_ts": 0,
            "cooldown_start_ts": 0,
            # tmux_target 设了就让 watchdog 走 tmux send-keys 复活到 pane;
            # 空串则 fallback 到旧 Popen 行为 (老部署兼容)。
            "tmux_target": router_tmux_target,
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

