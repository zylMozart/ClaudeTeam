"""Integration tests for stage 2 detector wired into wake.py.

Verifies the ``CLAUDETEAM_DETECTOR_LEGACY=1`` grayscale flag flips between
the new ``AgentDetector`` path and the legacy ``cli_pids_in_pane`` /proc
walk inside ``agent_has_live_cli`` and ``wait_cli_ui_ready``.

Run::

    PYTHONPATH=src python3 tests/test_wake_detector_integration.py
"""
from __future__ import annotations

import os
import sys
import unittest
from unittest import mock

_HERE = os.path.dirname(os.path.abspath(__file__))
_SRC = os.path.join(os.path.dirname(_HERE), "src")
if _SRC not in sys.path:
    sys.path.insert(0, _SRC)


class WakeDetectorFlagTests(unittest.TestCase):
    def setUp(self):
        # Each test starts with a clean env (no LEGACY flag).
        self._env_patch = mock.patch.dict(os.environ, {}, clear=False)
        self._env_patch.start()
        os.environ.pop("CLAUDETEAM_DETECTOR_LEGACY", None)

    def tearDown(self):
        self._env_patch.stop()

    def test_default_path_uses_detector(self):
        from claudeteam.messaging.router import wake
        from claudeteam.runtime.agent_detector import AgentDetector

        detector_alive_called = {"count": 0}

        def fake_is_alive(self):
            detector_alive_called["count"] += 1
            return True

        legacy_called = {"count": 0}

        def fake_cli_pids(*args, **kwargs):
            legacy_called["count"] += 1
            return [1234]

        with mock.patch.object(AgentDetector, "is_alive", fake_is_alive), \
             mock.patch.object(wake, "cli_pids_in_pane", fake_cli_pids):
            result = wake.agent_has_live_cli(
                "manager",
                "session",
                get_process_name=lambda n: "claude",
                get_process_names=lambda n: {"claude", "node"},
            )

        self.assertTrue(result)
        self.assertEqual(detector_alive_called["count"], 1)
        self.assertEqual(legacy_called["count"], 0)

    def test_legacy_flag_uses_cli_pids_in_pane(self):
        from claudeteam.messaging.router import wake
        from claudeteam.runtime.agent_detector import AgentDetector

        os.environ["CLAUDETEAM_DETECTOR_LEGACY"] = "1"

        detector_called = {"count": 0}

        def fake_is_alive(self):
            detector_called["count"] += 1
            return True

        legacy_called = {"args": None}

        def fake_cli_pids(agent_name, tmux_session, *, get_process_name):
            legacy_called["args"] = (agent_name, tmux_session, get_process_name("manager"))
            return [9999]

        with mock.patch.object(AgentDetector, "is_alive", fake_is_alive), \
             mock.patch.object(wake, "cli_pids_in_pane", fake_cli_pids):
            result = wake.agent_has_live_cli(
                "manager",
                "session",
                get_process_name=lambda n: "claude",
                get_process_names=lambda n: {"claude", "node"},
            )

        self.assertTrue(result)
        self.assertEqual(detector_called["count"], 0)
        self.assertEqual(legacy_called["args"], ("manager", "session", "claude"))

    def test_detector_path_falls_back_to_single_name_set(self):
        # If callers haven't been updated to pass get_process_names,
        # the detector path still works using {get_process_name(...)}.
        from claudeteam.messaging.router import wake
        from claudeteam.runtime.agent_detector import AgentDetector

        captured = {}

        def fake_init(self, session, agent, *, process_names=None, **kw):
            captured["names"] = process_names

        def fake_is_alive(self):
            return False

        with mock.patch.object(AgentDetector, "__init__", fake_init), \
             mock.patch.object(AgentDetector, "is_alive", fake_is_alive):
            wake.agent_has_live_cli(
                "manager",
                "session",
                get_process_name=lambda n: "claude",
                # No get_process_names passed → detector receives single-elem set.
            )

        self.assertEqual(captured["names"], {"claude"})

    def test_wait_ready_legacy_when_no_tmux_session(self):
        # When tmux_session is omitted (old caller path), wait_cli_ui_ready
        # falls through to the markers-based legacy implementation regardless
        # of the LEGACY flag.
        from claudeteam.messaging.router import wake

        called = {"markers": None}

        class _R:
            ok = False
            reason = "shell_prompt"

        def fake_legacy(capture_fn, markers, *, process_name="", timeout_s=30):
            called["markers"] = markers
            return _R()

        # wake.py aliases the import as _wait_cli_ui_ready — patch the
        # alias on the wake module, not tmux_utils, so the call resolves
        # to our fake.
        with mock.patch.object(wake, "_wait_cli_ui_ready", fake_legacy):
            r = wake.wait_cli_ui_ready(
                "manager",
                capture_pane_fn=lambda n: "",
                get_ready_markers=lambda n: ["? for shortcuts"],
                get_process_name=lambda n: "claude",
                # tmux_session not passed → legacy
                timeout_s=0.01,
            )
        self.assertEqual(called["markers"], ["? for shortcuts"])

    def test_wait_ready_uses_detector_when_session_provided(self):
        from claudeteam.messaging.router import wake
        from claudeteam.runtime.agent_detector import AgentDetector, ReadyProbe

        def fake_wait(self, *, timeout_s, **kwargs):
            return ReadyProbe(is_ready=True, waited_secs=0.0, reason="ok")

        with mock.patch.object(AgentDetector, "wait_until_ready", fake_wait):
            r = wake.wait_cli_ui_ready(
                "manager",
                capture_pane_fn=lambda n: "",
                get_ready_markers=lambda n: [],
                get_process_name=lambda n: "claude",
                get_process_names=lambda n: {"claude", "node"},
                tmux_session="session",
                timeout_s=0.01,
            )
        # Adapted to WakeReadyResult shape (uses ``ok`` not ``ready``).
        self.assertTrue(r.ok)
        self.assertTrue(bool(r))  # __bool__ delegates to .ok
        self.assertEqual(r.reason, "ok")


if __name__ == "__main__":
    unittest.main(verbosity=2)
