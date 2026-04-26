"""Handler for /health slash command — rendering only.

Subprocess collection lives in claudeteam.runtime.health.
collect_health(agent_set, session) is injected via SlashContext.
"""
from __future__ import annotations

import re
from .context import SlashContext


# ── /health handler ───────────────────────────────────────────────────────────

def handle_health(text: str, ctx: SlashContext) -> str | None:
    if not re.fullmatch(r"/health\s*", text):
        return None
    now_str = ctx.now_bj().strftime("%Y-%m-%d %H:%M 北京时间")
    collect = ctx.collect_health
    if collect is None:
        return f"⚠️ /health: collect_health 未注入（P3.5b 接线后可用）@ {now_str}"
    data = collect()
    return _format_health(data, now_str)


def _fmt_mem(b: int) -> str:
    if b >= 1024**3:
        return f"{b/1024**3:.2f} GB"
    if b >= 1024**2:
        return f"{b/1024**2:.0f} MB"
    return f"{b//1024} KB"


def _format_health(data: dict, now: str) -> str:
    lines = [f"🖥️ 服务器状态 @ {now}"]
    host = data.get("host", {})
    cpu = host.get("cpu")
    mem = host.get("mem")
    disk = host.get("disk")
    if cpu:
        l1, l5, l15 = cpu["load"]
        lines.append(f"  CPU: load {l1}/{l5}/{l15}  {cpu['pct']}%  ({cpu['cores']} cores)")
    if mem:
        lines.append(f"  内存: {mem['pct']}%  used {_fmt_mem(mem['used'])} / {_fmt_mem(mem['total'])}")
    if disk:
        lines.append(f"  磁盘 {disk['mount']}: {disk['pct']}%  {_fmt_mem(disk['used'])} / {_fmt_mem(disk['total'])}")
    for c in data.get("containers", []):
        lines.append(f"  容器 {c.get('short', c.get('name', '?'))}: "
                     f"cpu {c.get('cpu_pct', 0):.1f}%  mem {c.get('mem_pct', 0):.1f}%")
    alarms = data.get("alarms", [])
    if alarms:
        lines.append("  ⚠️ 告警: " + "; ".join(alarms[:3]))
    return "\n".join(lines)
