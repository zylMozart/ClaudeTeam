#!/usr/bin/env python3
"""No-live gate for watchdog burst/cooldown/recovery state transitions."""
from __future__ import annotations

import importlib
import os
import subprocess
import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
SCRIPTS = ROOT / "scripts"
TESTS = ROOT / "tests"
for path in (SCRIPTS, TESTS, ROOT):
    if str(path) not in sys.path:
        sys.path.insert(0, str(path))

from no_live_guard import install  # noqa: E402


def _reload_watchdog():
    if "watchdog" in sys.modules:
        return importlib.reload(sys.modules["watchdog"])
    return importlib.import_module("watchdog")


def _patch_watchdog_gate(
    watchdog,
    *,
    healthy_ref: dict[str, bool],
    now_ref: dict[str, float],
    restart_calls: list[str],
    notify_calls: list[str],
    cooldown_alerts: list[tuple[str, str]],
):
    originals = {
        "is_healthy": watchdog.is_healthy,
        "restart_process": watchdog.restart_process,
        "notify_manager": watchdog.notify_manager,
        "_send_manager_alert": watchdog._send_manager_alert,
        "log": watchdog.log,
        "time_time": watchdog.time.time,
        "time_sleep": watchdog.time.sleep,
        "subprocess_run": watchdog.subprocess.run,
        "os_kill": watchdog.os.kill,
    }

    def _fake_is_healthy(_proc):
        return healthy_ref["value"]

    def _fake_restart_process(proc):
        restart_calls.append(proc["name"])
        proc["last_restart_ts"] = now_ref["value"]

    def _fake_notify_manager(proc_name):
        notify_calls.append(proc_name)

    def _fake_send_manager_alert(msg, log_label):
        cooldown_alerts.append((msg, log_label))

    def _fake_log(_msg):
        return None

    def _fake_time():
        return now_ref["value"]

    def _fake_sleep(_secs):
        return None

    def _forbidden_subprocess_run(*args, **kwargs):
        raise AssertionError(f"watchdog gate forbids subprocess.run: {args!r} {kwargs!r}")

    def _forbidden_os_kill(*args, **kwargs):
        raise AssertionError(f"watchdog gate forbids os.kill: {args!r} {kwargs!r}")

    watchdog.is_healthy = _fake_is_healthy
    watchdog.restart_process = _fake_restart_process
    watchdog.notify_manager = _fake_notify_manager
    watchdog._send_manager_alert = _fake_send_manager_alert
    watchdog.log = _fake_log
    watchdog.time.time = _fake_time
    watchdog.time.sleep = _fake_sleep
    watchdog.subprocess.run = _forbidden_subprocess_run
    watchdog.os.kill = _forbidden_os_kill
    return originals


def _restore_watchdog_gate(watchdog, originals) -> None:
    watchdog.is_healthy = originals["is_healthy"]
    watchdog.restart_process = originals["restart_process"]
    watchdog.notify_manager = originals["notify_manager"]
    watchdog._send_manager_alert = originals["_send_manager_alert"]
    watchdog.log = originals["log"]
    watchdog.time.time = originals["time_time"]
    watchdog.time.sleep = originals["time_sleep"]
    watchdog.subprocess.run = originals["subprocess_run"]
    watchdog.os.kill = originals["os_kill"]


def test_watchdog_state_helper_contract_when_present() -> None:
    helper_file = ROOT / "src" / "claudeteam" / "supervision" / "watchdog_state.py"
    if not helper_file.exists():
        return

    side_effect_calls = []
    orig_subprocess_run = subprocess.run
    orig_os_kill = os.kill
    sys.modules.pop("claudeteam.supervision.watchdog_state", None)

    def _forbidden_subprocess_run(*args, **kwargs):
        side_effect_calls.append(("subprocess.run", args, kwargs))
        raise AssertionError("watchdog_state helper gate forbids subprocess.run")

    def _forbidden_os_kill(*args, **kwargs):
        side_effect_calls.append(("os.kill", args, kwargs))
        raise AssertionError("watchdog_state helper gate forbids os.kill")

    subprocess.run = _forbidden_subprocess_run
    os.kill = _forbidden_os_kill
    try:
        helper = importlib.import_module("claudeteam.supervision.watchdog_state")

        required_names = (
            "ACTION_HEALTHY",
            "ACTION_HEALTHY_RESET",
            "ACTION_COOLDOWN_WAIT",
            "ACTION_RESTART",
            "ACTION_ENTER_COOLDOWN",
            "WatchdogStateDecision",
            "decide_watchdog_state",
        )
        for name in required_names:
            assert hasattr(helper, name), f"watchdog_state missing {name}"
        assert callable(helper.decide_watchdog_state), "watchdog_state decide_watchdog_state not callable"

        proc = {"max_retries": 2, "cooldown_secs": 10, "retry_count": 0, "cooldown_start_ts": 0.0}
        d1 = helper.decide_watchdog_state(proc, healthy=False, now=1000.0)
        assert d1.action == helper.ACTION_RESTART, d1
        assert d1.retry_count == 1 and d1.cooldown_start_ts == 0.0, d1

        proc["retry_count"] = d1.retry_count
        proc["cooldown_start_ts"] = d1.cooldown_start_ts
        d2 = helper.decide_watchdog_state(proc, healthy=False, now=1001.0)
        assert d2.action == helper.ACTION_RESTART, d2
        assert d2.retry_count == 2 and d2.cooldown_start_ts == 0.0, d2

        proc["retry_count"] = d2.retry_count
        proc["cooldown_start_ts"] = d2.cooldown_start_ts
        d3 = helper.decide_watchdog_state(proc, healthy=False, now=1002.0)
        assert d3.action == helper.ACTION_ENTER_COOLDOWN, d3
        assert d3.retry_count == 3 and d3.cooldown_start_ts == 1002.0, d3

        proc["retry_count"] = d3.retry_count
        proc["cooldown_start_ts"] = d3.cooldown_start_ts
        d4 = helper.decide_watchdog_state(proc, healthy=False, now=1005.0)
        assert d4.action == helper.ACTION_COOLDOWN_WAIT, d4
        assert d4.retry_count == 3 and d4.cooldown_start_ts == 1002.0, d4
        assert d4.cooldown_remaining_secs == 7, d4

        d5 = helper.decide_watchdog_state(proc, healthy=False, now=1013.0)
        assert d5.action == helper.ACTION_RESTART, d5
        assert d5.cooldown_ended is True, d5
        assert d5.retry_count == 1 and d5.cooldown_start_ts == 0.0, d5

        d6 = helper.decide_watchdog_state(
            {"max_retries": 2, "cooldown_secs": 10, "retry_count": 1, "cooldown_start_ts": 0.0},
            healthy=True,
            now=1014.0,
        )
        assert d6.action == helper.ACTION_HEALTHY_RESET, d6
        assert d6.retry_count == 0 and d6.cooldown_start_ts == 0.0, d6

        d7 = helper.decide_watchdog_state(
            {"max_retries": 2, "cooldown_secs": 10, "retry_count": 0, "cooldown_start_ts": 0.0},
            healthy=True,
            now=1015.0,
        )
        assert d7.action == helper.ACTION_HEALTHY, d7
        assert d7.retry_count == 0 and d7.cooldown_start_ts == 0.0, d7
    finally:
        subprocess.run = orig_subprocess_run
        os.kill = orig_os_kill

    assert not side_effect_calls, f"watchdog_state helper triggered side effects: {side_effect_calls!r}"


def test_check_once_burst_cooldown_and_auto_recovery() -> None:
    watchdog = _reload_watchdog()
    original_procs = watchdog.PROCS
    proc = {
        "name": "watchdog-gate-proc",
        "max_retries": 2,
        "cooldown_secs": 10,
        "retry_count": 0,
        "last_restart_ts": 0,
        "cooldown_start_ts": 0,
    }
    watchdog.PROCS = [proc]

    healthy_ref = {"value": False}
    now_ref = {"value": 1000.0}
    restart_calls: list[str] = []
    notify_calls: list[str] = []
    cooldown_alerts: list[tuple[str, str]] = []
    originals = _patch_watchdog_gate(
        watchdog,
        healthy_ref=healthy_ref,
        now_ref=now_ref,
        restart_calls=restart_calls,
        notify_calls=notify_calls,
        cooldown_alerts=cooldown_alerts,
    )
    try:
        watchdog.check_once()
        assert proc["retry_count"] == 1, proc
        assert proc["cooldown_start_ts"] == 0, proc
        assert restart_calls == ["watchdog-gate-proc"], restart_calls
        assert notify_calls == [], notify_calls
        assert cooldown_alerts == [], cooldown_alerts

        now_ref["value"] = 1001.0
        watchdog.check_once()
        assert proc["retry_count"] == 2, proc
        assert proc["cooldown_start_ts"] == 0, proc
        assert restart_calls == ["watchdog-gate-proc", "watchdog-gate-proc"], restart_calls
        assert notify_calls == [], notify_calls
        assert cooldown_alerts == [], cooldown_alerts

        now_ref["value"] = 1002.0
        watchdog.check_once()
        assert proc["retry_count"] == 3, proc
        assert proc["cooldown_start_ts"] == 1002.0, proc
        assert restart_calls == ["watchdog-gate-proc", "watchdog-gate-proc"], restart_calls
        assert notify_calls == [], notify_calls
        assert len(cooldown_alerts) == 1, cooldown_alerts
        assert "cooldown" in cooldown_alerts[0][0], cooldown_alerts

        now_ref["value"] = 1005.0
        watchdog.check_once()
        assert proc["retry_count"] == 3, proc
        assert proc["cooldown_start_ts"] == 1002.0, proc
        assert restart_calls == ["watchdog-gate-proc", "watchdog-gate-proc"], restart_calls
        assert notify_calls == [], notify_calls
        assert len(cooldown_alerts) == 1, cooldown_alerts

        now_ref["value"] = 1013.0
        watchdog.check_once()
        assert proc["retry_count"] == 1, proc
        assert proc["cooldown_start_ts"] == 0, proc
        assert restart_calls == [
            "watchdog-gate-proc",
            "watchdog-gate-proc",
            "watchdog-gate-proc",
        ], restart_calls
        assert notify_calls == [], notify_calls
        assert len(cooldown_alerts) == 1, cooldown_alerts
    finally:
        _restore_watchdog_gate(watchdog, originals)
        watchdog.PROCS = original_procs


def test_check_once_healthy_resets_retry_and_cooldown() -> None:
    watchdog = _reload_watchdog()
    original_procs = watchdog.PROCS
    proc = {
        "name": "watchdog-gate-reset",
        "max_retries": 3,
        "cooldown_secs": 60,
        "retry_count": 2,
        "last_restart_ts": 0,
        "cooldown_start_ts": 777.0,
    }
    watchdog.PROCS = [proc]

    healthy_ref = {"value": True}
    now_ref = {"value": 2000.0}
    restart_calls: list[str] = []
    notify_calls: list[str] = []
    cooldown_alerts: list[tuple[str, str]] = []
    originals = _patch_watchdog_gate(
        watchdog,
        healthy_ref=healthy_ref,
        now_ref=now_ref,
        restart_calls=restart_calls,
        notify_calls=notify_calls,
        cooldown_alerts=cooldown_alerts,
    )
    try:
        watchdog.check_once()
        assert proc["retry_count"] == 0, proc
        assert proc["cooldown_start_ts"] == 0, proc
        assert restart_calls == [], restart_calls
        assert notify_calls == [], notify_calls
        assert cooldown_alerts == [], cooldown_alerts

        healthy_ref["value"] = False
        now_ref["value"] = 2001.0
        watchdog.check_once()
        assert proc["retry_count"] == 1, proc
        assert proc["cooldown_start_ts"] == 0, proc
        assert restart_calls == ["watchdog-gate-reset"], restart_calls
        assert notify_calls == [], notify_calls
        assert cooldown_alerts == [], cooldown_alerts
    finally:
        _restore_watchdog_gate(watchdog, originals)
        watchdog.PROCS = original_procs


def main() -> int:
    install()
    test_watchdog_state_helper_contract_when_present()
    test_check_once_burst_cooldown_and_auto_recovery()
    test_check_once_healthy_resets_retry_and_cooldown()
    print("OK: watchdog_state_machine_gate passed")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
