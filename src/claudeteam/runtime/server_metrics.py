"""Live server-load data collection (subprocess layer).

R166 ports `feat/messaging-fixes-block1` / `main`'s `runtime/health.py`
collector so the rebuild branch can render the same rich `/health`
card the boss recognised — host CPU/mem/disk + docker container stats
+ per-agent process tree. Subprocess calls live here so
`feishu/slash._handle_health` stays pure / testable.

Data shape returned by `collect_server_load(agent_set, session)`:

    {
        "host": {
            "cpu": {"load": (1m, 5m, 15m), "cores": int, "pct": int} | None,
            "mem": {"total": int, "used": int, "available": int,
                    "pct": int, "swap": {"total": int, "used": int}} | None,
            "disk": {"mount": str, "used": int, "total": int, "pct": int} | None,
        },
        "containers": [{"name": str, "short": str, "cpu_pct": float,
                        "mem_pct": float, "mem_used": int, "status": str}, ...],
        "agents": [{"agent": str, "location": str, "cpu": float, "mem": int}, ...],
        "alarms": [str, ...],
    }

`None`-valued host entries (uptime/free/df not available — Docker
Desktop on macOS host commands return non-zero in some cases) are
handled by the card builder which falls back to "无数据" cells.

Sister to `commands/health.py` (deploy-checker / config sanity); this
module is the live-metrics collector that the slash card consumes.
"""
from __future__ import annotations

import json
import re
import subprocess
from collections import defaultdict
from typing import Callable

from claudeteam.util import fmt_bytes

_SIZE_UNIT = {"K": 1024, "KI": 1024, "M": 1024**2, "MI": 1024**2,
              "G": 1024**3, "GI": 1024**3, "T": 1024**4, "TI": 1024**4}


def _run(cmd: list[str], timeout: int = 5) -> subprocess.CompletedProcess:
    """Best-effort subprocess wrapper. Returns a stand-in object on
    failure so callers can branch on `returncode != 0` uniformly."""
    try:
        return subprocess.run(cmd, capture_output=True, text=True,
                              timeout=timeout)
    except (FileNotFoundError, subprocess.TimeoutExpired, OSError) as e:
        class _R:
            returncode = -1
            stdout = ""
            stderr = str(e)
        return _R()


def _parse_size(s: str) -> int:
    """Parse `12.5GiB` / `420MB` / `1024K` etc. into bytes. Returns 0
    for anything unparseable."""
    m = re.match(r"([\d.]+)\s*([KMGT]i?)?B?\s*", s or "")
    if not m:
        return 0
    return int(float(m.group(1))
               * _SIZE_UNIT.get((m.group(2) or "").upper(), 1))


# ── host metrics ────────────────────────────────────────────────


def _host_cpu(run: Callable = _run) -> dict | None:
    r = run(["uptime"])
    if r.returncode != 0:
        return None
    m = re.search(r"load average:\s*([\d.]+),\s*([\d.]+),\s*([\d.]+)",
                  r.stdout)
    if not m:
        return None
    l1, l5, l15 = (float(m.group(i)) for i in (1, 2, 3))
    n = run(["nproc"])
    try:
        ncores = int((n.stdout or "").strip() or "1")
    except ValueError:
        ncores = 1
    return {"load": (l1, l5, l15), "cores": ncores,
            "pct": int(round(l1 / max(ncores, 1) * 100))}


def _host_mem(run: Callable = _run) -> dict | None:
    r = run(["free", "-b"])
    if r.returncode != 0:
        return None
    mem = swap = None
    for line in r.stdout.splitlines():
        parts = line.split()
        if parts and parts[0] == "Mem:" and len(parts) >= 7:
            mem = {"total": int(parts[1]), "used": int(parts[2]),
                   "available": int(parts[6])}
        elif parts and parts[0] == "Swap:" and len(parts) >= 3:
            swap = {"total": int(parts[1]), "used": int(parts[2])}
    if not mem:
        return None
    mem["pct"] = int(round(mem["used"] / max(mem["total"], 1) * 100))
    mem["swap"] = swap or {"total": 0, "used": 0}
    return mem


def _host_disk(run: Callable = _run) -> dict | None:
    """Worst-case mount point by % used. Skips tmpfs/devtmpfs/overlay
    so the result reflects actual storage rather than RAM-disks."""
    r = run(["df", "-B1", "-x", "tmpfs", "-x", "devtmpfs", "-x", "overlay"])
    if r.returncode != 0:
        return None
    worst = None
    for line in r.stdout.splitlines()[1:]:
        parts = line.split()
        if len(parts) < 6:
            continue
        try:
            total = int(parts[1])
            used = int(parts[2])
            pct = int(parts[4].rstrip("%"))
        except ValueError:
            continue
        if worst is None or pct > worst["pct"]:
            worst = {"mount": parts[5], "used": used, "total": total, "pct": pct}
    return worst


# ── docker container stats ─────────────────────────────────────


def _docker_stats(run: Callable = _run) -> list[dict]:
    """`docker stats --no-stream` for claudeteam-* containers. Empty
    list when docker isn't available (which is the common case inside
    the container itself — no docker socket mounted)."""
    r = run(["sudo", "-n", "docker", "stats", "--no-stream",
             "--format", "{{json .}}"], timeout=15)
    if r.returncode != 0:
        return []
    status_r = run(["sudo", "-n", "docker", "ps", "--format",
                    "{{.Names}}\t{{.Status}}"])
    status_map: dict[str, str] = {}
    for line in status_r.stdout.splitlines():
        name, _, status = line.partition("\t")
        if name.startswith("claudeteam-"):
            status_map[name] = status
    out: list[dict] = []
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
            mem_used = _parse_size(mu.split("/")[0].strip() if "/" in mu
                                   else mu.strip())
        except (ValueError, AttributeError):
            cpu = mem_pct = 0.0
            mem_used = 0
        out.append({
            "name": name,
            "short": name.replace("claudeteam-", "").replace("-team-1", ""),
            "cpu_pct": cpu, "mem_pct": mem_pct, "mem_used": mem_used,
            "status": status_map.get(name, ""),
        })
    return out


# ── per-agent process tree (CPU/RSS for each tmux pane's pid subtree)


def _parse_ps_tree(text: str) -> tuple[dict, dict]:
    """Parse `ps -eo pid,ppid,pcpu,rss` output. Returns (procs, children)
    where procs[pid] = (ppid, pcpu, rss_kb) and children[ppid] = [pid, ...]."""
    procs: dict[int, tuple] = {}
    children: dict[int, list[int]] = defaultdict(list)
    for line in (text or "").splitlines()[1:]:
        parts = line.split(None, 3)
        if len(parts) < 4:
            continue
        try:
            pid = int(parts[0])
            ppid = int(parts[1])
            pcpu = float(parts[2])
            rss_kb = int(parts[3])
        except ValueError:
            continue
        procs[pid] = (ppid, pcpu, rss_kb)
        children[ppid].append(pid)
    return procs, children


def _subtree_usage(root_pid: int, procs: dict,
                   children: dict) -> tuple[float, int]:
    """Walk the subtree rooted at `root_pid`; sum CPU% and RSS bytes."""
    cpu = 0.0
    rss = 0
    seen: set[int] = set()
    stack = [root_pid]
    while stack:
        pid = stack.pop()
        if pid in seen or pid not in procs:
            continue
        seen.add(pid)
        _, c, r = procs[pid]
        cpu += c
        rss += r
        stack.extend(children.get(pid, []))
    return cpu, rss * 1024


def _agent_usage(agent_set: frozenset, session: str,
                 run: Callable = _run) -> list[dict]:
    """Per-agent CPU%/RSS by walking the tmux pane's pid subtree.

    Maps each tmux window in the configured session to its pane pid via
    `tmux list-panes`, then sums the descendant process tree. Empty list
    when tmux or ps isn't available.
    """
    r = run(["tmux", "list-panes", "-a", "-F",
             "#{session_name}:#{window_name} #{pane_pid}"])
    if r.returncode != 0:
        return []
    panes: dict[str, int] = {}
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
    ps = run(["ps", "-eo", "pid,ppid,pcpu,rss"])
    procs, children = _parse_ps_tree(ps.stdout)
    out: list[dict] = []
    for agent, pid in panes.items():
        cpu, mem = _subtree_usage(pid, procs, children)
        out.append({"agent": agent, "location": session,
                    "cpu": cpu, "mem": mem})
    return out


# ── alarm aggregator ──────────────────────────────────────────


def _collect_alarms(host_mem: dict | None, host_disk: dict | None,
                    containers: list[dict],
                    run: Callable = _run) -> list[str]:
    """Surface red-flag conditions: high host mem/disk %, container
    pressure, and recent kernel OOM messages. Each entry is a markdown
    string ready to drop into the card body."""
    alarms: list[str] = []
    if host_mem and host_mem["pct"] >= 90:
        alarms.append(
            f"主机内存 **{host_mem['pct']}%**"
            f"（used {fmt_bytes(host_mem['used'])}）")
    if host_disk and host_disk["pct"] >= 80:
        alarms.append(
            f"磁盘 `{host_disk['mount']}` **{host_disk['pct']}%**")
    for c in containers:
        if c["mem_pct"] >= 90:
            alarms.append(
                f"容器 `{c['short']}` 内存 **{c['mem_pct']:.1f}%**")
        if c["cpu_pct"] >= 80:
            alarms.append(
                f"容器 `{c['short']}` CPU **{c['cpu_pct']:.1f}%**")
    dm = run(["dmesg", "-T"], timeout=3)
    if dm.returncode == 0 and dm.stdout:
        oom = [line for line in dm.stdout.splitlines()[-500:]
               if re.search(r"out of memory|killed process", line, re.I)]
        if oom:
            alarms.append(
                f"内核 OOM/killed 记录 {len(oom)} 条（tail 3）：\n  "
                + "\n  ".join(oom[-3:]))
    return alarms


# ── public API ────────────────────────────────────────────────


def collect_server_load(agent_set: frozenset,
                        session: str,
                        run: Callable = _run) -> dict:
    """Live server-load snapshot for the `/health` slash card.

    `agent_set` is the team's agent name set (used to filter tmux
    panes); `session` is the tmux session name (used as agent location
    label). `run` is injectable for tests so the data flow can be
    exercised without spawning real subprocesses.
    """
    cpu = _host_cpu(run=run)
    mem = _host_mem(run=run)
    disk = _host_disk(run=run)
    containers = _docker_stats(run=run)
    agents = _agent_usage(agent_set, session, run=run)
    agents.sort(key=lambda a: a["cpu"], reverse=True)
    alarms = _collect_alarms(mem, disk, containers, run=run)
    return {
        "host": {"cpu": cpu, "mem": mem, "disk": disk},
        "containers": containers,
        "agents": agents,
        "alarms": alarms,
    }
