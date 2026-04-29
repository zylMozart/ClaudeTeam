"""Unit tests for the refactored ``watchdog.is_healthy`` (router_autoheal_design §2.2).

Two-gate semantics covered:
  • Gate 1 (liveness): pid_file alive, pid_file dead but pgrep alive, both dead,
    no pid_file (process matched by pgrep only).
  • Gate 2 (heartbeat): no health_file → pass on liveness; with health_file the
    decision goes through ``_watchdog_health.decide_health_file_state`` (cold-
    start grace, missing file = indeterminate, stale mtime → unhealthy).

Pgrep fallback case (the explicit refactor target) is in
``test_pgrep_fallback_then_stale_heartbeat_returns_unhealthy``: when the PID
file is dead but pgrep finds the process AND the health file is stale, the new
structure makes the unhealthy verdict explicit (not relying on accidental
fall-through in the old layered ``if`` ladder).

Run::
    PYTHONPATH=src python3 tests/test_watchdog_is_healthy.py
"""
import os
import sys
import tempfile
import time
import unittest
from unittest import mock

_HERE = os.path.dirname(os.path.abspath(__file__))
_ROOT = os.path.dirname(_HERE)
_SRC = os.path.join(_ROOT, "src")
_SCRIPTS = os.path.join(_ROOT, "scripts")
for p in (_SRC, _SCRIPTS):
    if p not in sys.path:
        sys.path.insert(0, p)

# Import watchdog as a module by file path; can't `import watchdog` directly
# because importing scripts/watchdog.py executes module-level signal handlers
# and pid-lock setup. We need a fresh import for each test.
import importlib.util


def _load_watchdog():
    spec = importlib.util.spec_from_file_location(
        "wdog_under_test", os.path.join(_SCRIPTS, "watchdog.py")
    )
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


class IsHealthyTwoGateTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.wdog = _load_watchdog()

    def setUp(self):
        # Each test gets a fresh tempdir for pid_file + health_file
        self._tmp = tempfile.TemporaryDirectory()
        self.tmpdir = self._tmp.name

    def tearDown(self):
        self._tmp.cleanup()

    def _proc(self, **overrides):
        spec = {
            "name": "router-test",
            "match": "feishu_router.py",
            "pid_file": os.path.join(self.tmpdir, "router.pid"),
            "health_file": os.path.join(self.tmpdir, "router.cursor"),
            "health_stale_secs": 90,
            "restart_grace_secs": 60,
            "last_restart_ts": 0,
        }
        spec.update(overrides)
        return spec

    def _write_pid(self, pid_file, pid):
        with open(pid_file, "w") as f:
            f.write(str(pid))

    def _touch_cursor(self, cursor_file, age_secs=0):
        with open(cursor_file, "w") as f:
            f.write("")
        if age_secs:
            past = time.time() - age_secs
            os.utime(cursor_file, (past, past))

    # ── Gate 1 dead path ──────────────────────────────────────────
    def test_dead_pid_file_no_pgrep_returns_unhealthy(self):
        proc = self._proc()
        with mock.patch.object(self.wdog, "is_running_by_pid_file", return_value=False), \
             mock.patch.object(self.wdog, "_pgrep_alive", return_value=False):
            self.assertFalse(self.wdog.is_healthy(proc))

    # ── Gate 1 happy + Gate 2 happy ──────────────────────────────
    def test_pid_alive_fresh_heartbeat_returns_healthy(self):
        proc = self._proc()
        self._write_pid(proc["pid_file"], 99999)
        self._touch_cursor(proc["health_file"], age_secs=10)
        with mock.patch.object(self.wdog, "is_running_by_pid_file", return_value=True):
            self.assertTrue(self.wdog.is_healthy(proc))

    # ── Gate 2 stale ─────────────────────────────────────────────
    def test_pid_alive_stale_heartbeat_returns_unhealthy(self):
        proc = self._proc()
        self._write_pid(proc["pid_file"], 99999)
        self._touch_cursor(proc["health_file"], age_secs=300)  # > 90s
        with mock.patch.object(self.wdog, "is_running_by_pid_file", return_value=True):
            self.assertFalse(self.wdog.is_healthy(proc))

    # ── Gate 1 pgrep fallback + Gate 2 still applies (THE REFACTOR TARGET) ──
    def test_pgrep_fallback_then_stale_heartbeat_returns_unhealthy(self):
        """The explicit fix: pgrep finding the process must NOT short-circuit
        past the heartbeat freshness check.
        """
        proc = self._proc()
        # pid_file says the named PID is dead, but pgrep finds something else
        # bearing the cmdline match. Heartbeat is stale → must be unhealthy.
        self._touch_cursor(proc["health_file"], age_secs=300)
        with mock.patch.object(self.wdog, "is_running_by_pid_file", return_value=False), \
             mock.patch.object(self.wdog, "_pgrep_alive", return_value=True):
            self.assertFalse(self.wdog.is_healthy(proc))

    def test_pgrep_fallback_then_fresh_heartbeat_returns_healthy(self):
        proc = self._proc()
        self._touch_cursor(proc["health_file"], age_secs=10)
        with mock.patch.object(self.wdog, "is_running_by_pid_file", return_value=False), \
             mock.patch.object(self.wdog, "_pgrep_alive", return_value=True):
            self.assertTrue(self.wdog.is_healthy(proc))

    # ── No health_file specs (e.g. kanban_sync) ──────────────────
    def test_no_health_file_falls_through_on_liveness_alone(self):
        proc = self._proc(health_file=None)
        self._write_pid(proc["pid_file"], 99999)
        with mock.patch.object(self.wdog, "is_running_by_pid_file", return_value=True):
            self.assertTrue(self.wdog.is_healthy(proc))

    # ── Cold-start grace ─────────────────────────────────────────
    def test_recent_restart_grace_skips_heartbeat_check(self):
        # restart_grace_secs=60; last_restart_ts ~= now → skip mtime check
        proc = self._proc(last_restart_ts=time.time() - 10)
        self._write_pid(proc["pid_file"], 99999)
        # Heartbeat is stale, but we're inside the grace window → still healthy.
        self._touch_cursor(proc["health_file"], age_secs=300)
        with mock.patch.object(self.wdog, "is_running_by_pid_file", return_value=True):
            self.assertTrue(self.wdog.is_healthy(proc))

    # ── Missing health file ──────────────────────────────────────
    def test_missing_health_file_outside_grace_returns_healthy(self):
        # When the file is missing AND we're past restart_grace, decide_health
        # treats age_secs=None as indeterminate (skip=False, stale=False) →
        # returns True. Documents existing behavior: a brand-new spawn whose
        # cursor file hasn't been written yet shouldn't be killed for it.
        proc = self._proc()
        self._write_pid(proc["pid_file"], 99999)
        # Don't touch cursor — file missing.
        with mock.patch.object(self.wdog, "is_running_by_pid_file", return_value=True):
            self.assertTrue(self.wdog.is_healthy(proc))


class WatchdogSpecsThresholdTests(unittest.TestCase):
    """Threshold-tightening regression: 180→90 / 120→60 (router_autoheal §2.2.2)."""

    def test_router_thresholds_match_design(self):
        from claudeteam.supervision.watchdog_specs import build_process_specs
        specs = build_process_specs(
            lark_cli=["lark-cli"],
            router_pid_file="/tmp/router.pid",
            router_cursor_file="/tmp/router.cursor",
            kanban_pid_file="/tmp/kanban.pid",
        )
        router = next(s for s in specs if "router" in s["name"])
        self.assertEqual(router["health_stale_secs"], 90)
        self.assertEqual(router["restart_grace_secs"], 60)


if __name__ == "__main__":
    unittest.main(verbosity=2)
