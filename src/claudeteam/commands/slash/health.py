"""Handler for /health slash command.

The heavy system-stats collection (CPU/mem/disk/docker) is performed by
injected callables on SlashContext so this module stays pure and testable.
"""
from __future__ import annotations

import re
from .context import SlashContext


def handle_health(text: str, ctx: SlashContext) -> str | None:
    if not re.fullmatch(r"/health\s*", text):
        return None
    now_str = ctx.now_bj().strftime("%Y-%m-%d %H:%M 北京时间")
    # Delegate data collection to injected callable (live impl wired in P3.5b)
    collect = getattr(ctx, "collect_health", None)
    if collect is None:
        return f"⚠️ /health: collect_health 未注入（P3.5b 接线后可用）@ {now_str}"
    data = collect()
    return _format_health(data, now_str)


def _format_health(data: dict, now: str) -> str:
    lines = [f"🖥️ 服务器状态 @ {now}"]
    if "host_cpu" in data:
        lines.append(f"  CPU: {data['host_cpu']}")
    if "host_mem" in data:
        lines.append(f"  内存: {data['host_mem']}")
    if "host_disk" in data:
        lines.append(f"  磁盘: {data['host_disk']}")
    if "containers" in data:
        for c in data["containers"]:
            lines.append(f"  容器 {c.get('name', '?')}: {c.get('status', '?')}")
    return "\n".join(lines)
