"""Handler for /health slash command."""
from __future__ import annotations

import re
import subprocess
from .context import SlashContext


def _run(cmd, timeout=5):
    try:
        return subprocess.run(cmd, capture_output=True, text=True, timeout=timeout)
    except Exception as e:
        class R:
            returncode = -1
            stdout = ""
            stderr = str(e)
        return R()


def handle_health(text: str, ctx: SlashContext) -> dict | str | None:
    if not re.fullmatch(r"/health\s*", text):
        return None
    now = ctx.now_bj().strftime("%Y-%m-%d %H:%M")
    collect = ctx.collect_health
    if collect is None:
        return f"⚠️ /health: collect_health 未注入（P3.5b 接线后可用）@ {now} 北京时间"
    data = collect()
    return {"text": build_server_load_text(data, now), "card": build_server_load_card(data, now)}


def _fmt_mem(b: int) -> str:
    if b >= 1024**3:
        return f"{b/1024**3:.2f} GB"
    if b >= 1024**2:
        return f"{b/1024**2:.0f} MB"
    if b >= 1024:
        return f"{b/1024:.0f} KB"
    return f"{b} B"


def _load_color(pct: int) -> str:
    if pct >= 80:
        return "red"
    if pct >= 50:
        return "orange"
    return "green"


def _hostname(run_fn=_run) -> str:
    r = run_fn(["hostname"])
    return (r.stdout or "localhost").strip()


def _emoji_for_agent_cpu(cpu: float) -> str:
    if cpu >= 80:
        return "🔥"
    if cpu >= 30:
        return "🔄"
    if cpu >= 5:
        return "⚙️"
    return "💤"


def _col_cell(content: str) -> dict:
    return {"tag": "column", "width": "weighted", "weight": 1,
            "elements": [{"tag": "markdown", "content": content}]}


def _col_set_3(cells: list) -> dict:
    cols = [_col_cell(c) for c in cells]
    while len(cols) < 3:
        cols.append(_col_cell(" "))
    return {"tag": "column_set", "flex_mode": "none", "background_style": "default", "columns": cols}


def build_server_load_card(data: dict, now: str, run_fn=_run) -> dict:
    host = data["host"]
    cpu = host["cpu"]
    mem = host["mem"]
    disk = host["disk"]
    containers = data["containers"]
    agents = data["agents"]
    alarms = data["alarms"]
    elements = []
    cpu_cell = ("**CPU**\n<font color='grey'>无数据</font>" if not cpu else
                f"**CPU**\n<font color='{_load_color(cpu['pct'])}'>**{cpu['load'][0]:.2f} / {cpu['cores']} 核 ({cpu['pct']}%)**</font>\n"
                f"<font color='grey'>5m {cpu['load'][1]:.2f} · 15m {cpu['load'][2]:.2f}</font>")
    mem_cell = ("**内存**\n<font color='grey'>无数据</font>" if not mem else
                f"**内存**\n<font color='{_load_color(mem['pct'])}'>**{_fmt_mem(mem['used'])} / {_fmt_mem(mem['total'])} ({mem['pct']}%)**</font>\n"
                f"<font color='grey'>可用 {_fmt_mem(mem['available'])} · Swap {_fmt_mem(mem['swap']['used'])}/{_fmt_mem(mem['swap']['total'])}</font>")
    disk_cell = ("**磁盘**\n<font color='grey'>无数据</font>" if not disk else
                 f"**磁盘** `{disk['mount']}`\n<font color='{_load_color(disk['pct'])}'>**{_fmt_mem(disk['used'])} / {_fmt_mem(disk['total'])} ({disk['pct']}%)**</font>")
    elements.append({"tag": "div", "text": {"tag": "lark_md", "content": "**🖥️ 主机总览**"}})
    elements.append(_col_set_3([cpu_cell, mem_cell, disk_cell]))
    elements.append({"tag": "hr"})
    if containers:
        running = sum(1 for c in containers if c["status"])
        total_cpu = sum(c["cpu_pct"] for c in containers)
        total_mem = sum(c["mem_used"] for c in containers)
        peak = max(containers, key=lambda c: c["mem_pct"])
        name_preview = " · ".join(c["short"] for c in containers[:3])
        if len(containers) > 3:
            name_preview += " …"
        elements.append({"tag": "div", "text": {"tag": "lark_md", "content": "**📦 团队容器总量**"}})
        elements.append(_col_set_3([
            f"**容器数**\n**{running} / {len(containers)}** 运行中\n<font color='grey'>{name_preview}</font>",
            f"**容器 CPU 合计**\n<font color='{_load_color(int(total_cpu))}'>**{total_cpu:.1f}%**</font>\n<font color='grey'>跨 {len(containers)} 容器加总</font>",
            f"**容器内存合计**\n**{_fmt_mem(total_mem)}**\n<font color='{_load_color(int(peak['mem_pct']))}'>峰值 `{peak['short']}` {peak['mem_pct']:.1f}%</font>",
        ]))
        elements.append({"tag": "hr"})
    if agents:
        topn = agents[:9]
        elements.append({"tag": "div", "text": {"tag": "lark_md", "content": f"**👤 员工细分 Top {len(topn)} / 共 {len(agents)}**（按 CPU 降序）"}})
        for i in range(0, len(topn), 3):
            cells = [f"{_emoji_for_agent_cpu(a['cpu'])} **{a['agent']}**\nCPU `{a['cpu']:.1f}%` · Mem `{_fmt_mem(a['mem'])}`\n<font color='grey'>{a['location']}</font>" for a in topn[i:i + 3]]
            elements.append(_col_set_3(cells))
        if len(agents) > 9:
            elements.append({"tag": "div", "text": {"tag": "lark_md", "content": f"<font color='grey'>… 另有 {len(agents) - 9} 个员工未显示</font>"}})
        elements.append({"tag": "hr"})
    if alarms:
        body = "\n".join(f"- <font color='red'>⚠️ {a}</font>" for a in alarms)
        elements.append({"tag": "div", "text": {"tag": "lark_md", "content": f"**🚨 异常告警**\n{body}"}})
        elements.append({"tag": "hr"})
    elements.append({"tag": "note", "elements": [{"tag": "plain_text", "content": f"采集 {now} 北京时间 · 数据源 uptime/free/df/docker stats/ps"}]})
    return {"config": {"wide_screen_mode": True}, "header": {"template": "purple", "title": {"tag": "plain_text", "content": f"🖥️ 服务器负载 · {_hostname(run_fn)} · {now}"}}, "elements": elements}


def build_server_load_text(data: dict, now: str, run_fn=_run) -> str:
    lines = [f"🖥️ /health — {_hostname(run_fn)} ({now} 北京时间)\n"]
    host = data["host"]
    if host["cpu"]:
        c = host["cpu"]
        lines.append(f"CPU: load {c['load'][0]:.2f}/{c['cores']} 核 ({c['pct']}%) · 5m {c['load'][1]:.2f} · 15m {c['load'][2]:.2f}")
    if host["mem"]:
        m = host["mem"]
        lines.append(f"内存: {_fmt_mem(m['used'])}/{_fmt_mem(m['total'])} ({m['pct']}%) · 可用 {_fmt_mem(m['available'])}")
    if host["disk"]:
        d = host["disk"]
        lines.append(f"磁盘 {d['mount']}: {_fmt_mem(d['used'])}/{_fmt_mem(d['total'])} ({d['pct']}%)")
    if data["containers"]:
        lines.append(f"\n容器 {len(data['containers'])}: CPU 合 {sum(c['cpu_pct'] for c in data['containers']):.1f}% · 内存合 {_fmt_mem(sum(c['mem_used'] for c in data['containers']))}")
    if data["agents"]:
        lines.append(f"\n员工 Top 9 / 共 {len(data['agents'])}:")
        for a in data["agents"][:9]:
            lines.append(f"  {a['agent']:<10} CPU {a['cpu']:5.1f}% · Mem {_fmt_mem(a['mem']):>9} · {a['location']}")
    if data["alarms"]:
        lines.append("\n⚠️ 告警:")
        for alarm in data["alarms"]:
            lines.append(f"  - {alarm}")
    return "\n".join(lines)
