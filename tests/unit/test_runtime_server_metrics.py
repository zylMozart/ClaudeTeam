"""Tests for runtime/server_metrics.py — host CPU/mem/disk/docker/agent
collector that backs the R166 /health card."""
from __future__ import annotations

from helpers import FakeProc
from claudeteam.runtime import server_metrics


def _stub_run(responses: dict):
    """Build a fake `run` that returns canned FakeProc by command-prefix
    match. `responses` keys are the first 1-2 argv tokens joined by ' '
    (so 'uptime' / 'free -b' / 'df -B1' / 'docker stats' / 'ps -eo'
    each get their own canned stdout)."""
    def fake_run(cmd, timeout=5):
        key = " ".join(cmd[:2]) if len(cmd) >= 2 else cmd[0]
        for k, proc in responses.items():
            if key.startswith(k) or " ".join(cmd[:3]).startswith(k):
                return proc
        return FakeProc(returncode=1)
    return fake_run


# ── _host_cpu ─────────────────────────────────────────────────


def _no_proc(_path):
    """Stub `read_proc` to simulate macOS host (no /proc) so the
    uptime/nproc fallback path runs and exercises the test stubs."""
    return None


def test_host_cpu_reads_proc_loadavg_when_present():
    """R172 primary path: /proc/loadavg directly read so it works
    inside the slim Docker image without procps."""
    proc_data = {"/proc/loadavg": "0.42 0.85 1.23 1/123 1234\n"}
    cpu = server_metrics._host_cpu(
        read_proc=lambda p: proc_data.get(p),
        cpu_count=lambda: 4,
    )
    assert cpu["load"] == (0.42, 0.85, 1.23)
    assert cpu["cores"] == 4
    # pct = round(0.42 / 4 * 100) = 10 (rounds half to even)
    assert cpu["pct"] == 10


def test_host_cpu_falls_back_to_uptime_when_proc_missing():
    """macOS host: /proc/loadavg returns None, code falls back to
    `uptime` shell-out (which exists on macOS)."""
    run = _stub_run({
        "uptime": FakeProc(stdout=" 13:30:00 up 2 days,  load average: 1.23, 0.85, 0.42\n"),
        "nproc": FakeProc(stdout="8\n"),
    })
    cpu = server_metrics._host_cpu(run=run, read_proc=_no_proc)
    assert cpu["load"] == (1.23, 0.85, 0.42)
    assert cpu["cores"] == 8
    assert cpu["pct"] == 15


def test_host_cpu_returns_none_when_no_proc_and_no_uptime():
    run = _stub_run({})
    assert server_metrics._host_cpu(run=run, read_proc=_no_proc) is None


def test_host_cpu_uses_cpu_count_when_nproc_returns_garbage():
    run = _stub_run({
        "uptime": FakeProc(stdout=" load average: 0.5, 0.3, 0.2\n"),
        "nproc": FakeProc(stdout="not-a-number"),
    })
    cpu = server_metrics._host_cpu(run=run, read_proc=_no_proc,
                                     cpu_count=lambda: 2)
    assert cpu["cores"] == 2


# ── _host_mem ─────────────────────────────────────────────────


def test_host_mem_reads_proc_meminfo_when_present():
    """R172 primary path: /proc/meminfo parse so /health works
    inside slim Docker images (no `free` binary)."""
    meminfo = (
        "MemTotal:       16384000 kB\n"   # 16 GB
        "MemFree:         2048000 kB\n"
        "MemAvailable:    8192000 kB\n"   # 8 GB available
        "Buffers:          512000 kB\n"
        "Cached:          5000000 kB\n"
        "SwapTotal:       2097152 kB\n"   # 2 GB swap total
        "SwapFree:        1048576 kB\n"   # 1 GB swap free → 1 GB used
    )
    proc_data = {"/proc/meminfo": meminfo}
    mem = server_metrics._host_mem(read_proc=lambda p: proc_data.get(p))
    assert mem["total"] == 16384000 * 1024  # bytes
    # used = total - available = 16384000kB - 8192000kB = 8192000 kB
    assert mem["used"] == 8192000 * 1024
    assert mem["available"] == 8192000 * 1024
    assert mem["pct"] == 50
    assert mem["swap"]["total"] == 2097152 * 1024
    assert mem["swap"]["used"] == 1048576 * 1024


def test_host_mem_returns_none_when_no_proc_and_no_vm_stat():
    """macOS host without /proc: falls back to `vm_stat`. If that's
    missing too (or stub returns failure), bail out None."""
    run = _stub_run({})
    assert server_metrics._host_mem(run=run, read_proc=_no_proc) is None


def test_host_mem_falls_back_to_vm_stat_on_macos():
    """macOS fallback path: parses `vm_stat` page counts."""
    vm_out = (
        "Mach Virtual Memory Statistics: (page size of 4096 bytes)\n"
        "Pages free:                  100000.\n"
        "Pages active:                400000.\n"
        "Pages inactive:              200000.\n"
        "Pages speculative:            50000.\n"
        "Pages wired down:            300000.\n"
    )
    run = _stub_run({"vm_stat": FakeProc(stdout=vm_out)})
    mem = server_metrics._host_mem(run=run, read_proc=_no_proc)
    # total = (100k + 400k + 200k + 50k + 300k) * 4096 = 1.05M * 4096
    assert mem["total"] == (100000 + 400000 + 200000 + 50000 + 300000) * 4096
    # used = active + wired = (400k + 300k) * 4096
    assert mem["used"] == (400000 + 300000) * 4096


# ── _host_disk ───────────────────────────────────────────────


def test_host_disk_picks_worst_mount_by_pct():
    df_out = (
        "Filesystem     1B-blocks         Used Available Use% Mounted on\n"
        "/dev/sda1   500000000000 100000000000 400000000000  20% /\n"
        "/dev/sdb1   100000000000  85000000000  15000000000  85% /var\n"
    )
    run = _stub_run({"df": FakeProc(stdout=df_out)})
    disk = server_metrics._host_disk(run=run)
    assert disk["mount"] == "/var"
    assert disk["pct"] == 85
    assert disk["used"] == 85000000000


# ── _docker_stats ────────────────────────────────────────────


def test_docker_stats_filters_to_claudeteam_prefix():
    """Names matching `claudeteam-*` are kept; others (system services
    sharing the host) are dropped. `short` strips both the leading
    `claudeteam-` and the compose-default `-team-1` suffix when both
    are present (e.g. `claudeteam-foo-team-1` → `foo`); the simpler
    case `claudeteam-team-1` keeps the bare `-team-1` since the
    suffix isn't preceded by a `-`."""
    stats_out = (
        '{"Name":"claudeteam-foo-team-1","CPUPerc":"5.2%","MemPerc":"35.1%",'
        '"MemUsage":"500MiB / 2GiB"}\n'
        '{"Name":"unrelated-service-1","CPUPerc":"99.9%","MemPerc":"99.9%",'
        '"MemUsage":"7GiB / 8GiB"}\n'
        '{"Name":"claudeteam-other-1","CPUPerc":"1.0%","MemPerc":"5.0%",'
        '"MemUsage":"100MiB / 2GiB"}\n'
    )
    ps_out = (
        "claudeteam-foo-team-1\tUp 5 hours\n"
        "unrelated-service-1\tUp 1 day\n"
        "claudeteam-other-1\tUp 2 hours\n"
    )
    def stateful_run(cmd, timeout=5):
        if cmd[:2] == ["sudo", "-n"] and len(cmd) >= 3 and cmd[2] == "docker":
            if "stats" in cmd:
                return FakeProc(stdout=stats_out)
            if "ps" in cmd:
                return FakeProc(stdout=ps_out)
        return FakeProc(returncode=1)
    out = server_metrics._docker_stats(run=stateful_run)
    names = sorted(c["name"] for c in out)
    assert names == ["claudeteam-foo-team-1", "claudeteam-other-1"]
    team = next(c for c in out if c["name"] == "claudeteam-foo-team-1")
    assert team["short"] == "foo"  # claudeteam-foo-team-1 → foo
    assert team["cpu_pct"] == 5.2
    assert team["status"] == "Up 5 hours"


def test_docker_stats_returns_empty_when_docker_unavailable():
    run = _stub_run({})  # no responses → all rc=1
    assert server_metrics._docker_stats(run=run) == []


# ── _parse_size ──────────────────────────────────────────────


def test_parse_size_handles_units():
    assert server_metrics._parse_size("12.5GiB") == int(12.5 * 1024**3)
    assert server_metrics._parse_size("420MB") == 420 * 1024**2
    assert server_metrics._parse_size("1024K") == 1024 * 1024
    assert server_metrics._parse_size("100B") == 100
    # Plain number — no unit
    assert server_metrics._parse_size("500") == 500


def test_parse_size_returns_zero_on_garbage():
    assert server_metrics._parse_size("") == 0
    assert server_metrics._parse_size("not a size") == 0


# ── collect_server_load (top-level) ─────────────────────────


def test_collect_server_load_returns_full_data_shape():
    """Smoke-test the public collector returns the expected dict shape
    even when most subprocess calls fail (the common Docker Desktop
    macOS host case where uptime/free aren't visible)."""
    run = _stub_run({})
    data = server_metrics.collect_server_load(
        agent_set=frozenset(["manager"]), session="ContainerA", run=run)
    assert set(data.keys()) == {"host", "containers", "agents", "alarms"}
    assert set(data["host"].keys()) == {"cpu", "mem", "disk"}
    # All None when run returns rc=1
    assert data["host"] == {"cpu": None, "mem": None, "disk": None}
    assert data["containers"] == []
    assert data["agents"] == []
    assert data["alarms"] == []


def test_collect_server_load_sorts_agents_by_cpu_desc():
    """Agents from host + containers concat, sorted by CPU% descending —
    boss reads top-3 CPU consumers off the top."""
    # Stub host_agent_usage indirectly via tmux + ps responses
    tmux_out = "ContainerA:manager 100\nContainerA:worker_cc 200\n"
    ps_out = (
        "  PID  PPID %CPU   RSS\n"
        "  100     1  5.0  500000\n"
        "  200     1 25.0 1000000\n"
    )
    def stateful_run(cmd, timeout=5):
        if cmd[0] == "tmux" and cmd[1] == "list-panes":
            return FakeProc(stdout=tmux_out)
        if cmd[0] == "ps":
            return FakeProc(stdout=ps_out)
        return FakeProc(returncode=1)
    data = server_metrics.collect_server_load(
        agent_set=frozenset(["manager", "worker_cc"]),
        session="ContainerA", run=stateful_run)
    cpus = [a["cpu"] for a in data["agents"]]
    assert cpus == sorted(cpus, reverse=True)
    assert data["agents"][0]["agent"] == "worker_cc"  # 25% > 5%


# ── alarm thresholds ────────────────────────────────────────


def test_alarms_fire_above_thresholds():
    high_mem = {"pct": 95, "used": 15 * 1024**3, "total": 16 * 1024**3,
                "available": 1 * 1024**3, "swap": {"total": 0, "used": 0}}
    full_disk = {"mount": "/", "pct": 92, "used": 460 * 1024**3,
                 "total": 500 * 1024**3}
    pressure_container = {"name": "claudeteam-x", "short": "x",
                          "cpu_pct": 95.0, "mem_pct": 91.0,
                          "mem_used": 1024**3, "status": ""}
    run = _stub_run({})  # dmesg unavailable
    alarms = server_metrics._collect_alarms(
        high_mem, full_disk, [pressure_container], run=run)
    blob = " | ".join(alarms)
    assert "主机内存" in blob and "95%" in blob
    assert "磁盘" in blob and "92%" in blob
    assert "容器 `x` 内存" in blob
    assert "容器 `x` CPU" in blob


def test_alarms_silent_when_under_thresholds():
    healthy_mem = {"pct": 30, "used": 5 * 1024**3, "total": 16 * 1024**3,
                   "available": 11 * 1024**3, "swap": {"total": 0, "used": 0}}
    healthy_disk = {"mount": "/", "pct": 40, "used": 200 * 1024**3,
                    "total": 500 * 1024**3}
    quiet_container = {"name": "claudeteam-x", "short": "x",
                       "cpu_pct": 5.0, "mem_pct": 30.0,
                       "mem_used": 1024**3, "status": ""}
    run = _stub_run({})
    alarms = server_metrics._collect_alarms(
        healthy_mem, healthy_disk, [quiet_container], run=run)
    assert alarms == []
