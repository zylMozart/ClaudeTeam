"""Live server-load collection (subprocess layer).

All subprocess calls live here so slash/health.py stays pure/testable.
"""
from __future__ import annotations

import json
import re
import subprocess
from collections import defaultdict

_SIZE_UNIT = {"K": 1024, "KI": 1024, "M": 1024**2, "MI": 1024**2,
              "G": 1024**3, "GI": 1024**3, "T": 1024**4, "TI": 1024**4}


def _run(cmd, timeout=5):
    try:
        return subprocess.run(cmd, capture_output=True, text=True, timeout=timeout)
    except Exception as e:
        class R:
            returncode = -1; stdout = ""; stderr = str(e)
        return R()


def _parse_size(s: str) -> int:
    m = re.match(r"([\d.]+)\s*([KMGT]i?)?B?\s*", s or "")
    if not m:
        return 0
    return int(float(m.group(1)) * _SIZE_UNIT.get((m.group(2) or "").upper(), 1))


def _host_cpu():
    r = _run(["uptime"])
    if r.returncode != 0:
        return None
    m = re.search(r"load average:\s*([\d.]+),\s*([\d.]+),\s*([\d.]+)", r.stdout)
    if not m:
        return None
    l1, l5, l15 = (float(m.group(i)) for i in (1, 2, 3))
    n = _run(["nproc"])
    try:
        ncores = int((n.stdout or "").strip() or "1")
    except ValueError:
        ncores = 1
    return {"load": (l1, l5, l15), "cores": ncores,
            "pct": int(round(l1 / max(ncores, 1) * 100))}


def _host_mem():
    r = _run(["free", "-b"])
    if r.returncode != 0:
        return None
    mem = swap = None
    for line in r.stdout.splitlines():
        parts = line.split()
        if parts and parts[0] == "Mem:" and len(parts) >= 7:
            mem = {"total": int(parts[1]), "used": int(parts[2]), "available": int(parts[6])}
        elif parts and parts[0] == "Swap:" and len(parts) >= 3:
            swap = {"total": int(parts[1]), "used": int(parts[2])}
    if not mem:
        return None
    mem["pct"] = int(round(mem["used"] / max(mem["total"], 1) * 100))
    mem["swap"] = swap or {"total": 0, "used": 0}
    return mem


def _host_disk():
    r = _run(["df", "-B1", "-x", "tmpfs", "-x", "devtmpfs", "-x", "overlay"])
    if r.returncode != 0:
        return None
    worst = None
    for line in r.stdout.splitlines()[1:]:
        parts = line.split()
        if len(parts) < 6:
            continue
        try:
            total, used, pct = int(parts[1]), int(parts[2]), int(parts[4].rstrip("%"))
        except ValueError:
            continue
        if worst is None or pct > worst["pct"]:
            worst = {"mount": parts[5], "used": used, "total": total, "pct": pct}
    return worst


def _docker_stats():
    r = _run(["sudo", "-n", "docker", "stats", "--no-stream",
              "--format", "{{json .}}"], timeout=15)
    if r.returncode != 0:
        return []
    status_r = _run(["sudo", "-n", "docker", "ps", "--format", "{{.Names}}\t{{.Status}}"])
    status_map = {}
    for line in status_r.stdout.splitlines():
        name, _, status = line.partition("\t")
        if name.startswith("claudeteam-"):
            status_map[name] = status
    out = []
    for line in r.stdout.splitlines():
        try:
            d = json.loads(line)
        except json.JSONDecodeError:
            continue
        name = d.get("Name", "")
        if not name.startswith("claudeteam-"):
            continue
        try:
            cpu = float(d.get("CPUPerc", "0").rstrip("%"))
            mem_pct = float(d.get("MemPerc", "0").rstrip("%"))
            mu = d.get("MemUsage", "")
            mem_used = _parse_size(mu.split("/")[0].strip() if "/" in mu else mu.strip())
        except Exception:
            cpu = mem_pct = 0.0; mem_used = 0
        out.append({"name": name,
                    "short": name.replace("claudeteam-", "").replace("-team-1", ""),
                    "cpu_pct": cpu, "mem_pct": mem_pct, "mem_used": mem_used,
                    "status": status_map.get(name, "")})
    return out


def _parse_ps_tree(text: str):
    procs = {}
    children = defaultdict(list)
    for line in (text or "").splitlines()[1:]:
        parts = line.split(None, 3)
        if len(parts) < 4:
            continue
        try:
            pid, ppid, pcpu, rss_kb = int(parts[0]), int(parts[1]), float(parts[2]), int(parts[3])
        except ValueError:
            continue
        procs[pid] = (ppid, pcpu, rss_kb)
        children[ppid].append(pid)
    return procs, children


def _subtree_usage(root_pid, procs, children):
    cpu = 0.0; rss = 0; seen = set(); stack = [root_pid]
    while stack:
        pid = stack.pop()
        if pid in seen or pid not in procs:
            continue
        seen.add(pid)
        _, c, r = procs[pid]
        cpu += c; rss += r
        stack.extend(children.get(pid, []))
    return cpu, rss * 1024


def _host_agent_usage(agent_set, session):
    r = _run(["tmux", "list-panes", "-a", "-F",
              "#{session_name}:#{window_name} #{pane_pid}"])
    if r.returncode != 0:
        return []
    panes = {}
    for line in r.stdout.splitlines():
        parts = line.split()
        if len(parts) < 2:
            continue
        win = parts[0].partition(":")[2]
        if win in agent_set and win not in panes:
            try:
                panes[win] = int(parts[1])
            except ValueError:
                pass
    if not panes:
        return []
    ps = _run(["ps", "-eo", "pid,ppid,pcpu,rss"])
    procs, children = _parse_ps_tree(ps.stdout)
    return [{"agent": a, "location": session,
             "cpu": (u := _subtree_usage(pid, procs, children))[0], "mem": u[1]}
            for a, pid in panes.items()]


def collect_health(agent_set: frozenset, session: str) -> dict:
    """Collect live server load data. Returns dict for _format_health."""
    cpu = _host_cpu()
    mem = _host_mem()
    disk = _host_disk()
    containers = _docker_stats()
    agents = _host_agent_usage(agent_set, session)
    alarms = []
    if mem and mem["pct"] >= 90:
        alarms.append(f"主机内存 {mem['pct']}%")
    if disk and disk["pct"] >= 80:
        alarms.append(f"磁盘 {disk['mount']} {disk['pct']}%")
    return {"host": {"cpu": cpu, "mem": mem, "disk": disk},
            "containers": containers, "agents": agents, "alarms": alarms}
