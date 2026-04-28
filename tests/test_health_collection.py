#!/usr/bin/env python3
"""No-live unit tests for runtime health collection."""
from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
for p in (ROOT / "src",):
    if str(p) not in sys.path:
        sys.path.insert(0, str(p))

from claudeteam.runtime import health


class R:
    def __init__(self, returncode=0, stdout=""):
        self.returncode = returncode
        self.stdout = stdout
        self.stderr = ""


def test_collect_health_includes_container_agent_usage_and_oom(monkeypatch):
    def fake_run(cmd, timeout=5):
        if cmd == ["uptime"]:
            return R(stdout="load average: 0.50, 0.40, 0.30")
        if cmd == ["nproc"]:
            return R(stdout="2\n")
        if cmd == ["free", "-b"]:
            return R(stdout="Mem: 1000 950 0 0 0 50\nSwap: 100 10\n")
        if cmd[:2] == ["df", "-B1"]:
            return R(stdout="Filesystem 1B-blocks Used Available Use% Mounted on\n/dev/sda 1000 850 150 85% /\n")
        if cmd[:5] == ["sudo", "-n", "docker", "stats", "--no-stream"]:
            return R(stdout='{"Name":"claudeteam-alpha-team-1","CPUPerc":"81.5%","MemPerc":"91.0%","MemUsage":"512MiB / 1GiB"}\n')
        if cmd[:5] == ["sudo", "-n", "docker", "ps", "--format"]:
            return R(stdout="claudeteam-alpha-team-1\tUp 1 hour\n")
        if cmd[:6] == ["sudo", "-n", "docker", "exec", "claudeteam-alpha-team-1", "tmux"]:
            return R(stdout="S:coder 10\n")
        if cmd[:6] == ["sudo", "-n", "docker", "exec", "claudeteam-alpha-team-1", "ps"]:
            return R(stdout="PID PPID %CPU RSS\n10 1 12.5 100\n11 10 2.5 50\n")
        if cmd[:3] == ["tmux", "list-panes", "-a"]:
            return R(stdout="")
        if cmd == ["ps", "-eo", "pid,ppid,pcpu,rss"]:
            return R(stdout="PID PPID %CPU RSS\n")
        if cmd == ["dmesg", "-T"]:
            return R(stdout="[Mon] killed process 123 claude\n")
        return R(returncode=1)

    monkeypatch.setattr(health, "_run", fake_run)
    data = health.collect_health(frozenset({"coder"}), "Host")
    assert data["agents"] == [{"agent": "coder", "location": "alpha", "cpu": 15.0, "mem": 153600}]
    assert any("容器 `alpha` 内存" in alarm for alarm in data["alarms"])
    assert any("容器 `alpha` CPU" in alarm for alarm in data["alarms"])
    assert any("OOM/killed" in alarm for alarm in data["alarms"])


def main() -> int:
    tests = [v for k, v in sorted(globals().items()) if k.startswith("test_")]
    passed = failed = 0
    class MonkeyPatch:
        def setattr(self, obj, name, value):
            setattr(obj, name, value)
    for fn in tests:
        try:
            fn(MonkeyPatch())
            print(f"  ok {fn.__name__}")
            passed += 1
        except Exception as e:
            print(f"  fail {fn.__name__}: {e}")
            failed += 1
    print(f"\nhealth collection tests: {passed}/{passed + failed} passed")
    return 0 if failed == 0 else 1


if __name__ == "__main__":
    raise SystemExit(main())
