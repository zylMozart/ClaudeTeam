"""Unit tests for ``claudeteam.runtime.agent_detector``.

Covers the design's §6.1 unit grid:
  • ``normalize_pane`` invariants (ANSI strip, digit erasure, last-line drop).
  • Five-state liveness classification (UNKNOWN / SHELL / SPAWNING / LIVE / DEAD).
  • ``is_idle`` stable / streaming / capture-failure cases.
  • ``wait_until_ready`` ok / shell / dead / timeout reasons.
  • Env-var defaults (samples=10, interval_ms=300, legacy flag).

No real tmux; the ``StubRunner`` injects fixed ``returncode`` / ``stdout`` for
each command pattern. Run::

    PYTHONPATH=src python3 tests/test_agent_detector.py
"""
from __future__ import annotations

import os
import sys
import time
import types
import unittest
from unittest import mock

_HERE = os.path.dirname(os.path.abspath(__file__))
_SRC = os.path.join(os.path.dirname(_HERE), "src")
if _SRC not in sys.path:
    sys.path.insert(0, _SRC)

from claudeteam.runtime.agent_detector import (  # noqa: E402
    AgentDetector,
    AgentLiveness,
    IdleProbe,
    LivenessProbe,
    ReadyProbe,
    default_interval_ms,
    default_samples,
    legacy_mode_enabled,
    normalize_pane,
)


# ── Stub tmux runner ─────────────────────────────────────────────

class StubRunner:
    """Tiny replacement for ``subprocess.run`` driven by command-pattern matchers.

    Construct with ``StubRunner.builder()``, then chain methods to register
    canned responses for each tmux subcommand the test cares about.
    Anything else returns rc=1 (mimics tmux failure).
    """

    def __init__(self):
        self._has_session_rc = 0
        self._display_responses = {}      # fmt → list of stdouts (FIFO)
        self._captures = []               # list of stdouts (FIFO)

    @classmethod
    def builder(cls):
        return cls()

    def has_session(self, rc=0):
        self._has_session_rc = rc
        return self

    def display(self, fmt, *outs):
        # Append (not replace) so callers can build up a multi-poll script.
        self._display_responses.setdefault(fmt, []).extend(list(outs))
        return self

    def captures(self, *outs):
        self._captures.extend(outs)
        return self

    def __call__(self, cmd):
        if cmd[:2] == ["tmux", "has-session"]:
            return _Result(self._has_session_rc, "")
        if cmd[:2] == ["tmux", "display-message"]:
            # cmd: ["tmux", "display-message", "-t", target, "-p", fmt]
            fmt = cmd[5] if len(cmd) >= 6 else ""
            outs = self._display_responses.get(fmt, [])
            if not outs:
                return _Result(1, "")
            # Pop the head; once we're down to the last entry, retain it so
            # later polls in long-running loops keep getting the same answer.
            head = outs[0]
            if len(outs) > 1:
                outs.pop(0)
            return _Result(0, head)
        if cmd[:2] == ["tmux", "capture-pane"]:
            if not self._captures:
                return _Result(1, "")
            return _Result(0, self._captures.pop(0))
        return _Result(1, "")


class _Result:
    def __init__(self, rc, stdout):
        self.returncode = rc
        self.stdout = stdout
        self.stderr = ""


# ── normalize_pane ───────────────────────────────────────────────

class NormalizePaneTests(unittest.TestCase):
    def test_strips_ansi(self):
        s = "hi\x1b[31m red\x1b[0m"
        self.assertNotIn("\x1b", normalize_pane(s))

    def test_drops_carriage_returns(self):
        self.assertNotIn("\r", normalize_pane("a\r\nb\r\n"))

    def test_erases_digit_runs(self):
        # Numeric noise (timers, percentages, byte counters) is the dominant
        # source of false positives on streaming spinners. They must collapse.
        a = normalize_pane("loading 42%\n> prompt\n")
        b = normalize_pane("loading 99%\n> prompt\n")
        self.assertEqual(a, b)

    def test_drops_trailing_line(self):
        # Spinner / cursor parks on the last line — drop it for hash stability.
        text = "stable line\nspinner ⣾"
        self.assertEqual(normalize_pane(text), "stable line")

    def test_empty_input(self):
        self.assertEqual(normalize_pane(""), "")
        self.assertEqual(normalize_pane(None), "")  # type: ignore[arg-type]


# ── liveness ─────────────────────────────────────────────────────

class LivenessTests(unittest.TestCase):
    def _detector(self, runner, **kwargs):
        kwargs.setdefault("process_names", {"claude", "node"})
        return AgentDetector("S", "W", tmux_runner=runner, **kwargs)

    def test_unknown_when_window_missing(self):
        runner = StubRunner.builder().has_session(rc=1)
        d = self._detector(runner)
        probe = d.liveness()
        self.assertEqual(probe.liveness, AgentLiveness.UNKNOWN)
        self.assertEqual(probe.pane_pid, None)

    def test_shell(self):
        runner = (
            StubRunner.builder()
            .has_session(rc=0)
            .display("#{pane_pid}", "1234")
            .display("#{pane_current_command}", "zsh")
        )
        probe = self._detector(runner).liveness()
        self.assertEqual(probe.liveness, AgentLiveness.SHELL)
        self.assertEqual(probe.pane_current_command, "zsh")
        self.assertEqual(probe.pane_pid, 1234)

    def test_live_when_front_cmd_in_process_names(self):
        runner = (
            StubRunner.builder()
            .has_session(rc=0)
            .display("#{pane_pid}", "1234")
            .display("#{pane_current_command}", "claude")
        )
        probe = self._detector(runner).liveness()
        self.assertEqual(probe.liveness, AgentLiveness.LIVE)

    def test_live_via_node_wrapper(self):
        # process_names is a SET — wrapper names like "node" must match.
        runner = (
            StubRunner.builder()
            .has_session(rc=0)
            .display("#{pane_pid}", "1234")
            .display("#{pane_current_command}", "node")
        )
        probe = self._detector(runner).liveness()
        self.assertEqual(probe.liveness, AgentLiveness.LIVE)

    def test_spawning_intermediate_process(self):
        runner = (
            StubRunner.builder()
            .has_session(rc=0)
            .display("#{pane_pid}", "1234")
            .display("#{pane_current_command}", "python3")  # not in {claude, node}
        )
        probe = self._detector(runner).liveness()
        self.assertEqual(probe.liveness, AgentLiveness.SPAWNING)

    def test_dead_when_pane_pid_unreadable(self):
        runner = (
            StubRunner.builder()
            .has_session(rc=0)
            .display("#{pane_pid}", "")  # tmux returned empty
            .display("#{pane_current_command}", "claude")
        )
        probe = self._detector(runner).liveness()
        self.assertEqual(probe.liveness, AgentLiveness.DEAD)

    def test_is_alive_convenience(self):
        runner = (
            StubRunner.builder()
            .has_session(rc=0)
            .display("#{pane_pid}", "1")
            .display("#{pane_current_command}", "claude")
        )
        self.assertTrue(self._detector(runner).is_alive())

        runner2 = (
            StubRunner.builder()
            .has_session(rc=0)
            .display("#{pane_pid}", "1")
            .display("#{pane_current_command}", "zsh")
        )
        self.assertFalse(self._detector(runner2).is_alive())

    def test_unknown_process_names_set(self):
        # Detector with no process_names configured can never report LIVE.
        runner = (
            StubRunner.builder()
            .has_session(rc=0)
            .display("#{pane_pid}", "1")
            .display("#{pane_current_command}", "claude")
        )
        det = AgentDetector("S", "W", tmux_runner=runner)  # no process_names
        probe = det.liveness()
        self.assertEqual(probe.liveness, AgentLiveness.SPAWNING)


# ── is_idle ──────────────────────────────────────────────────────

class IsIdleTests(unittest.TestCase):
    def _detector(self, runner):
        return AgentDetector("S", "W", process_names={"claude"}, tmux_runner=runner)

    def test_stable_pane_is_idle(self):
        # 5 identical captures → idle.
        identical = "stable\n> prompt"
        runner = StubRunner.builder().captures(*([identical] * 5))
        with mock.patch("time.sleep"):
            probe = self._detector(runner).is_idle(samples=5, interval_ms=10)
        self.assertTrue(probe.is_idle)
        self.assertEqual(probe.sampled_frames, 5)
        self.assertTrue(probe.fingerprint)

    def test_changing_pane_is_busy(self):
        # 2nd frame differs → busy after 2 samples.
        runner = StubRunner.builder().captures(
            "frame one\nspinner",
            "frame TWO\nspinner",
        )
        with mock.patch("time.sleep"):
            probe = self._detector(runner).is_idle(samples=10, interval_ms=10)
        self.assertFalse(probe.is_idle)
        self.assertEqual(probe.sampled_frames, 2)

    def test_streaming_with_only_digit_changes_is_idle(self):
        # Spinner with rotating numbers (timer / percentage) — normalize_pane
        # erases digit runs, so hashes match even though raw text differs.
        # NOTE: detector drops the LAST line via normalize_pane, so put the
        # changing text in a non-final position.
        frames = [
            "loading 10%\nidle prompt",
            "loading 25%\nidle prompt",
            "loading 99%\nidle prompt",
        ]
        runner = StubRunner.builder().captures(*frames)
        with mock.patch("time.sleep"):
            probe = self._detector(runner).is_idle(samples=3, interval_ms=10)
        self.assertTrue(probe.is_idle, f"expected idle, got {probe!r}")

    def test_streaming_real_text_change_is_busy(self):
        # Output streaming with new lines = real change, not numeric noise.
        runner = StubRunner.builder().captures(
            "header\nline alpha",
            "header\nline alpha\nline beta",
        )
        with mock.patch("time.sleep"):
            probe = self._detector(runner).is_idle(samples=10, interval_ms=10)
        self.assertFalse(probe.is_idle)

    def test_capture_failure_returns_busy_fail_safe(self):
        # tmux capture failed → treat as busy (don't inject into broken pane).
        runner = StubRunner.builder()  # no captures registered → rc=1
        with mock.patch("time.sleep"):
            probe = self._detector(runner).is_idle(samples=5, interval_ms=10)
        self.assertFalse(probe.is_idle)
        self.assertEqual(probe.reason, "capture_pane failed")

    def test_single_sample_is_always_idle(self):
        # samples=1 has no diff to detect; default is idle by definition.
        runner = StubRunner.builder().captures("hello")
        probe = self._detector(runner).is_idle(samples=1, interval_ms=10)
        self.assertTrue(probe.is_idle)
        self.assertEqual(probe.sampled_frames, 1)


# ── wait_until_ready ─────────────────────────────────────────────

class WaitUntilReadyTests(unittest.TestCase):
    def _detector(self, runner):
        return AgentDetector("S", "W", process_names={"claude"}, tmux_runner=runner)

    def test_ok_when_live_and_placeholder_visible(self):
        runner = (
            StubRunner.builder()
            .has_session(rc=0)
            .display("#{pane_pid}", "1")
            .display("#{pane_current_command}", "claude")
            .captures("welcome\n? for shortcuts\n")
        )
        with mock.patch("time.sleep"):
            probe = self._detector(runner).wait_until_ready(
                timeout_s=1.0, poll_interval_s=0.01
            )
        self.assertTrue(probe.is_ready)
        self.assertEqual(probe.reason, "ok")

    def test_dead_returns_immediately(self):
        runner = (
            StubRunner.builder()
            .has_session(rc=0)
            .display("#{pane_pid}", "")  # → DEAD
            .display("#{pane_current_command}", "claude")
        )
        with mock.patch("time.sleep"):
            probe = self._detector(runner).wait_until_ready(
                timeout_s=1.0, poll_interval_s=0.01
            )
        self.assertFalse(probe.is_ready)
        self.assertEqual(probe.reason, "dead")

    def test_timeout_when_stuck_in_shell(self):
        # StubRunner repeats the last display response indefinitely, so a
        # single "1" / "zsh" pair is enough to cover any number of polls.
        runner = (
            StubRunner.builder()
            .has_session(rc=0)
            .display("#{pane_pid}", "1")
            .display("#{pane_current_command}", "zsh")
        )
        with mock.patch("time.sleep"):
            probe = self._detector(runner).wait_until_ready(
                timeout_s=0.05, poll_interval_s=0.01
            )
        self.assertFalse(probe.is_ready)
        self.assertEqual(probe.reason, "shell")


# ── Env defaults ─────────────────────────────────────────────────

class EnvDefaultsTests(unittest.TestCase):
    def test_default_samples_is_10(self):
        with mock.patch.dict(os.environ, {}, clear=False):
            os.environ.pop("CLAUDETEAM_IDLE_SAMPLE_COUNT", None)
            self.assertEqual(default_samples(), 10)

    def test_default_interval_is_300(self):
        with mock.patch.dict(os.environ, {}, clear=False):
            os.environ.pop("CLAUDETEAM_IDLE_SAMPLE_INTERVAL_MS", None)
            self.assertEqual(default_interval_ms(), 300)

    def test_env_overrides_samples(self):
        with mock.patch.dict(os.environ, {"CLAUDETEAM_IDLE_SAMPLE_COUNT": "5"}):
            self.assertEqual(default_samples(), 5)

    def test_invalid_env_falls_back(self):
        with mock.patch.dict(os.environ, {"CLAUDETEAM_IDLE_SAMPLE_COUNT": "garbage"}):
            self.assertEqual(default_samples(), 10)

    def test_legacy_flag_truthy_values(self):
        for v in ("1", "true", "yes", "on", "TRUE"):
            with mock.patch.dict(os.environ, {"CLAUDETEAM_DETECTOR_LEGACY": v}):
                self.assertTrue(legacy_mode_enabled(), f"value {v!r}")

    def test_legacy_flag_falsy_values(self):
        for v in ("", "0", "false", "off"):
            with mock.patch.dict(os.environ, {"CLAUDETEAM_DETECTOR_LEGACY": v}):
                self.assertFalse(legacy_mode_enabled(), f"value {v!r}")


if __name__ == "__main__":
    unittest.main(verbosity=2)
