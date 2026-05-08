"""Tests for runtime/watchdog.py — process supervision state machine."""
from __future__ import annotations

import signal
from pathlib import Path

from helpers import isolated_env
from claudeteam.runtime.watchdog import (
    ProcessSpec,
    ProcessState,
    default_specs,
    is_alive,
    list_orphan_pids,
    reap_orphans,
    respawn,
    supervise,
)


class _FakeRun:
    """Minimal subprocess.run stand-in producing a configurable CompletedProcess."""

    def __init__(self, stdout: str = "", returncode: int = 0,
                 raises: Exception | None = None):
        self.stdout = stdout
        self.returncode = returncode
        self.raises = raises
        self.calls: list[list[str]] = []

    def __call__(self, argv, **kw):
        self.calls.append(list(argv))
        if self.raises is not None:
            raise self.raises
        class _R:
            pass
        r = _R()
        r.stdout = self.stdout
        r.returncode = self.returncode
        return r


def _spec(**overrides) -> ProcessSpec:
    base = dict(
        name="router",
        pid_file=Path("/tmp/test.pid"),
        expected_cmdline="claudeteam router",
        spawn_cmd=["claudeteam", "router"],
        max_retries=3,
        cooldown_secs=600,
        orphan_markers=(),
    )
    base.update(overrides)
    return ProcessSpec(**base)


# ── is_alive ──────────────────────────────────────────────────────


def test_is_alive_false_when_pid_file_missing():
    spec = _spec()
    assert is_alive(
        spec,
        read_pid=lambda p: None,
        pid_alive=lambda pid: True,
        read_cmdline=lambda pid: "anything",
    ) is False


def test_is_alive_false_when_pid_dead():
    spec = _spec()
    assert is_alive(
        spec,
        read_pid=lambda p: 1234,
        pid_alive=lambda pid: False,
        read_cmdline=lambda pid: "claudeteam router",
    ) is False


def test_is_alive_false_when_cmdline_mismatched():
    """PID reuse defense: pid alive, but it's a different process."""
    spec = _spec()
    assert is_alive(
        spec,
        read_pid=lambda p: 1234,
        pid_alive=lambda pid: True,
        read_cmdline=lambda pid: "/usr/bin/firefox",
    ) is False


def test_is_alive_true_when_all_three_match():
    spec = _spec()
    assert is_alive(
        spec,
        read_pid=lambda p: 1234,
        pid_alive=lambda pid: True,
        read_cmdline=lambda pid: "python claudeteam router --foo",
    ) is True


# ── respawn ───────────────────────────────────────────────────────


def test_respawn_returns_true_when_popen_succeeds():
    spec = _spec()
    calls = []
    assert respawn(spec, popen=lambda *a, **k: calls.append((a, k)) or object()) is True
    assert calls
    args, kwargs = calls[0]
    assert args[0] == spec.spawn_cmd
    assert kwargs.get("start_new_session") is True


def test_respawn_returns_false_on_oserror():
    spec = _spec()

    def bad(*a, **k):
        raise OSError("nope")

    assert respawn(spec, popen=bad) is False


def test_respawn_uses_devnull_when_log_file_unset():
    """Default behavior: no log_file → stdout/stderr both DEVNULL.
    Mirrors pre-R178 contract for any spec that doesn't opt in."""
    import subprocess
    spec = _spec()
    captured = {}
    def spy(*a, **k):
        captured["stdout"] = k.get("stdout")
        captured["stderr"] = k.get("stderr")
        return object()
    assert respawn(spec, popen=spy) is True
    assert captured["stdout"] is subprocess.DEVNULL
    assert captured["stderr"] is subprocess.DEVNULL


def test_respawn_appends_to_log_file_when_set():
    """When spec.log_file is set, both stdout and stderr go to that file
    in append mode. Without this, transient daemon failures (router
    silently drops a slash, watchdog hits a Popen error) leave no trace.
    REGRESSION: 2026-05-06 /tmux worker_cc silent failures couldn't be
    diagnosed because router stdout was DEVNULL."""
    import os, tempfile
    from pathlib import Path
    with tempfile.TemporaryDirectory() as tmp:
        log_path = Path(tmp) / "logs" / "router.log"  # parent missing on purpose
        spec = _spec(log_file=log_path)
        captured = {}
        def spy(*a, **k):
            captured["stdout"] = k.get("stdout")
            captured["stderr"] = k.get("stderr")
            # Sanity: it's a file object opened in append mode for line buffering
            return object()
        assert respawn(spec, popen=spy) is True
        # parent dir created, file exists, both fds point to the same file
        assert log_path.parent.is_dir()
        assert captured["stdout"] is captured["stderr"]
        # subprocess passed it a real file-like with a fileno
        assert hasattr(captured["stdout"], "fileno")


def test_respawn_falls_back_to_devnull_when_log_file_open_fails():
    """Permission denied / disk full / parent-dir on a read-only mount
    shouldn't kill the respawn. Warn and use DEVNULL."""
    import contextlib, io, subprocess
    spec = _spec(log_file=Path("/dev/null/cant-write-here/router.log"))
    captured = {}
    def spy(*a, **k):
        captured["stdout"] = k.get("stdout")
        return object()
    out = io.StringIO()
    with contextlib.redirect_stdout(out):
        assert respawn(spec, popen=spy) is True
    assert captured["stdout"] is subprocess.DEVNULL
    assert "log_file open failed" in out.getvalue()


# ── orphan reap (round-65) ───────────────────────────────────────


_PS_HEADER = "  PID  PPID COMMAND\n"
# Realistic mac/linux ps snapshot. The orphan-root after a SIGKILL'd
# router is the `npm exec @larksuite/cli` process (it had npx as parent;
# npx already exited, so it reparents to launchd/init when router dies).
# Its child node binary keeps the orphan as parent (PPID != 1) until
# the orphan exits, so it doesn't directly count as orphan-root.
_PS_REAL_SAMPLE = _PS_HEADER + (
    "    1     0 /sbin/launchd\n"
    "  100     1 /usr/libexec/UserEventAgent\n"
    "95773     1 npm exec @larksuite/cli --profile test-live-a event +subscribe --as bot\n"
    "96397 95773 node /Users/x/.npm/_npx/.../lark-cli --profile test-live-a event +subscribe --as bot\n"
    "98765     1 npm exec @larksuite/cli --profile test-live-b event +subscribe --as bot\n"
    "99999     1 some-other-daemon --foo\n"
)


def test_list_orphan_pids_finds_lark_subscribe_with_ppid_one():
    """Orphan-detection sees only PPID=1 lark-cli +subscribe processes
    (not their non-orphan descendants, not unrelated daemons)."""
    fake = _FakeRun(stdout=_PS_REAL_SAMPLE)
    pids = list_orphan_pids(("@larksuite/cli", "+subscribe"), run=fake)
    # Both 95773 (team A) and 98765 (team B) are orphan-root npm-exec
    # processes whose parent reparented to launchd. 96397 has PPID 95773
    # so it's not an orphan-root. 100 and 99999 don't carry both markers.
    assert sorted(pids) == [95773, 98765]


def test_list_orphan_pids_returns_empty_when_markers_empty():
    """A spec with no orphan_markers MUST not invoke ps at all (saves the
    fork and avoids surprise on systems where ps is slow/missing)."""
    fake = _FakeRun(stdout="should not be read")
    assert list_orphan_pids((), run=fake) == []
    assert fake.calls == []


def test_list_orphan_pids_handles_ps_failure_gracefully():
    """Best-effort: a missing ps, a timeout, or a non-zero return all
    just yield an empty list — never raise (orphan reap is not
    load-bearing for liveness)."""
    for failure in (FileNotFoundError("ps"),
                    OSError("EPERM")):
        fake = _FakeRun(raises=failure)
        assert list_orphan_pids(("X", "Y"), run=fake) == []
    fake = _FakeRun(stdout="garbage", returncode=1)
    assert list_orphan_pids(("X", "Y"), run=fake) == []


def test_list_orphan_pids_skips_malformed_lines():
    """Lines without three columns or with non-int PID/PPID must be
    silently skipped, not blow up."""
    bad = _PS_HEADER + (
        "garbage\n"
        "  abc  def something\n"   # non-int PID/PPID
        "1     1\n"                # only two columns
        "2     1 hello world\n"    # valid but no marker match
    )
    fake = _FakeRun(stdout=bad)
    assert list_orphan_pids(("@larksuite/cli", "+subscribe"), run=fake) == []


def test_reap_orphans_sigterms_each_orphan():
    spec = _spec(orphan_markers=("@larksuite/cli", "+subscribe"))
    fake_run = _FakeRun(stdout=_PS_REAL_SAMPLE)
    killed: list[tuple[int, int]] = []
    n = reap_orphans(spec, run=fake_run,
                     kill=lambda pid, sig: killed.append((pid, sig)),
                     log=lambda *_: None)
    assert n == 2
    assert sorted(killed) == [(95773, signal.SIGTERM), (98765, signal.SIGTERM)]


def test_reap_orphans_tolerates_lookup_and_permission_errors():
    """Process exited between scan and kill, OR runs as a different uid:
    just count it as not-reaped, never raise."""
    spec = _spec(orphan_markers=("@larksuite/cli", "+subscribe"))
    fake_run = _FakeRun(stdout=_PS_REAL_SAMPLE)

    def angry_kill(pid, sig):
        if pid == 95773:
            raise ProcessLookupError()
        if pid == 98765:
            raise PermissionError()
        return None

    n = reap_orphans(spec, run=fake_run, kill=angry_kill, log=lambda *_: None)
    assert n == 0


def test_reap_orphans_returns_zero_for_empty_markers():
    spec = _spec(orphan_markers=())
    fake_run = _FakeRun(stdout="ignored")
    assert reap_orphans(spec, run=fake_run, kill=lambda *_: None,
                        log=lambda *_: None) == 0


def test_respawn_invokes_reap_before_spawning_when_markers_present():
    """The reap must happen BEFORE Popen — otherwise the new daemon's
    subscribe would race with the orphan's subscribe for events."""
    spec = _spec(orphan_markers=("@larksuite/cli", "+subscribe"))
    order: list[str] = []
    respawn(spec,
            popen=lambda *a, **k: order.append("popen") or object(),
            reap=lambda s: order.append("reap"))
    assert order == ["reap", "popen"]


def test_respawn_skips_reap_when_no_markers():
    """Specs without orphan_markers (e.g. watchdog itself) shouldn't
    trigger a ps scan — reap is still called but is a noop."""
    spec = _spec(orphan_markers=())
    reap_called: list[ProcessSpec] = []
    respawn(spec,
            popen=lambda *a, **k: object(),
            reap=lambda s: reap_called.append(s) or 0)
    # reap is invoked uniformly; the noop check is in list_orphan_pids
    assert reap_called == [spec]


# ── supervise: alive path ────────────────────────────────────────


def test_supervise_records_alive_when_check_passes():
    spec = _spec()
    states: dict = {}
    supervise([spec], states,
              alive_check=lambda s: True,
              respawn_fn=lambda s: True,
              now=lambda: 0,
              log=lambda *_: None)
    assert states["router"].last_action == "alive"
    assert states["router"].fail_count == 0


def test_supervise_resets_fail_count_on_alive_recovery():
    spec = _spec()
    states = {"router": ProcessState("router", fail_count=2)}
    supervise([spec], states,
              alive_check=lambda s: True,
              respawn_fn=lambda s: True,
              now=lambda: 0, log=lambda *_: None)
    assert states["router"].fail_count == 0


# ── supervise: respawn path ──────────────────────────────────────


def test_supervise_respawns_when_dead_and_no_cooldown():
    spec = _spec()
    states: dict = {}
    respawned = []
    supervise([spec], states,
              alive_check=lambda s: False,
              respawn_fn=lambda s: respawned.append(s.name) or True,
              now=lambda: 0, log=lambda *_: None)
    assert respawned == ["router"]
    assert states["router"].last_action == "respawned"


def test_supervise_increments_fail_count_when_respawn_returns_false():
    spec = _spec(max_retries=5)
    states: dict = {}
    supervise([spec], states,
              alive_check=lambda s: False,
              respawn_fn=lambda s: False,
              now=lambda: 0, log=lambda *_: None)
    assert states["router"].fail_count == 1
    assert states["router"].last_action == "fail"
    assert states["router"].cooldown_until == 0  # not in cooldown yet


def test_supervise_enters_cooldown_after_max_retries():
    spec = _spec(max_retries=2, cooldown_secs=600)
    states = {"router": ProcessState("router", fail_count=1)}
    supervise([spec], states,
              alive_check=lambda s: False,
              respawn_fn=lambda s: False,
              now=lambda: 1000.0, log=lambda *_: None)
    # 1 + 1 = 2 = max_retries; goes to cooldown
    assert states["router"].cooldown_until == 1600.0
    assert states["router"].last_action == "cooldown"
    assert states["router"].fail_count == 0  # reset


def test_supervise_calls_alert_fn_when_entering_cooldown():
    """Round-82: cooldown entry triggers alert_fn(name, failed_at, cooldown_secs)
    so callers can fan out to Feishu / pager / log."""
    spec = _spec(max_retries=2, cooldown_secs=600)
    states = {"router": ProcessState("router", fail_count=1)}
    alerts = []
    supervise([spec], states,
              alive_check=lambda s: False,
              respawn_fn=lambda s: False,
              alert_fn=lambda name, fc, cd: alerts.append((name, fc, cd)),
              now=lambda: 1000.0, log=lambda *_: None)
    # Pre-cooldown fail_count was 2 (1 + 1), passed as failed_at
    assert alerts == [("router", 2, 600)]


def test_supervise_no_alert_when_only_one_fail_no_cooldown_yet():
    """Single failure (still under max_retries) must not page the boss
    — only the cooldown transition is alert-worthy."""
    spec = _spec(max_retries=3, cooldown_secs=600)
    states: dict = {}
    alerts = []
    supervise([spec], states,
              alive_check=lambda s: False,
              respawn_fn=lambda s: False,
              alert_fn=lambda *a: alerts.append(a),
              now=lambda: 0.0, log=lambda *_: None)
    assert alerts == []
    assert states["router"].fail_count == 1


def test_supervise_swallows_alert_fn_exceptions():
    """A broken alert path must not kill supervise. Daemon liveness
    matters more than chat delivery."""
    spec = _spec(max_retries=1, cooldown_secs=60)
    states: dict = {}
    logs = []

    def broken_alert(*a):
        raise RuntimeError("network down")

    supervise([spec], states,
              alive_check=lambda s: False,
              respawn_fn=lambda s: False,
              alert_fn=broken_alert,
              now=lambda: 0.0, log=lambda msg: logs.append(msg))
    # Cooldown still entered correctly
    assert states["router"].last_action == "cooldown"
    # Warning logged so operators can grep
    assert any("alert_fn raised" in m for m in logs)


def test_supervise_skips_during_cooldown():
    spec = _spec()
    states = {"router": ProcessState("router", cooldown_until=2000.0)}
    respawned = []
    supervise([spec], states,
              alive_check=lambda s: False,  # would normally respawn
              respawn_fn=lambda s: respawned.append(s) or True,
              now=lambda: 1500.0,  # before cooldown_until
              log=lambda *_: None)
    assert respawned == []
    assert states["router"].last_action == "cooldown"


def test_supervise_resumes_after_cooldown_expires():
    spec = _spec()
    states = {"router": ProcessState("router", cooldown_until=1000.0)}
    respawned = []
    supervise([spec], states,
              alive_check=lambda s: False,
              respawn_fn=lambda s: respawned.append(s) or True,
              now=lambda: 1500.0,  # past cooldown
              log=lambda *_: None)
    assert len(respawned) == 1


# ── multi-process supervision ────────────────────────────────────


def test_supervise_walks_every_spec_independently():
    s1 = _spec(name="router")
    s2 = _spec(name="kanban", expected_cmdline="kanban_sync")
    states: dict = {}
    aliveness = {"router": True, "kanban": False}
    respawned = []
    supervise([s1, s2], states,
              alive_check=lambda s: aliveness[s.name],
              respawn_fn=lambda s: respawned.append(s.name) or True,
              now=lambda: 0, log=lambda *_: None)
    assert states["router"].last_action == "alive"
    assert states["kanban"].last_action == "respawned"
    assert respawned == ["kanban"]


# ── default_specs ────────────────────────────────────────────────


def test_default_specs_includes_router_pointing_at_state_dir():
    with isolated_env() as tmp:
        specs = default_specs()
        assert any(s.name == "router" for s in specs)
        router = next(s for s in specs if s.name == "router")
        assert str(router.pid_file).startswith(str(tmp))
        assert router.spawn_cmd == ["claudeteam", "router"]
        # Round-65: router spec ships with orphan-reap markers so the
        # watchdog reaps stale lark-cli +subscribe processes left by a
        # SIGKILL'd predecessor before respawning.
        assert "@larksuite/cli" in router.orphan_markers
        assert "+subscribe" in router.orphan_markers


def test_all_known_specs_router_has_orphan_markers_watchdog_does_not():
    """Only the router runs lark-cli +subscribe subprocesses, so only the
    router spec needs orphan reap. The watchdog spec must stay at empty
    markers — otherwise it would scan ps every supervise sweep for a
    process tree it never spawns."""
    from claudeteam.runtime.watchdog import all_known_specs
    with isolated_env():
        specs = {s.name: s for s in all_known_specs()}
    assert specs["router"].orphan_markers
    assert specs["watchdog"].orphan_markers == ()


def test_default_specs_does_not_include_watchdog():
    """default_specs is what the watchdog itself supervises — and the
    watchdog doesn't supervise itself (no infinite recursion). It only
    knows about router."""
    from claudeteam.runtime.watchdog import default_specs as _default
    with isolated_env():
        names = [s.name for s in _default()]
    assert names == ["router"]


def test_all_known_specs_includes_both_router_and_watchdog():
    """all_known_specs is the bigger list used by `claudeteam health`
    (and `claudeteam up` / `claudeteam down`) to enumerate every
    daemon ClaudeTeam ships. Includes the watchdog itself so health
    can verify its lock file matches a live process."""
    from claudeteam.runtime.watchdog import all_known_specs
    with isolated_env() as tmp:
        specs = all_known_specs()
        names = sorted(s.name for s in specs)
        assert names == ["router", "watchdog"]
        # Both pid files live under the isolated state_dir
        for s in specs:
            assert str(s.pid_file).startswith(str(tmp))
        # spawn_cmd shape is ["claudeteam", <name>]
        for s in specs:
            assert s.spawn_cmd == ["claudeteam", s.name]
        # Both expect the "claudeteam" cmdline marker (defends against
        # PID reuse — see ProcessSpec.expected_cmdline)
        for s in specs:
            assert s.expected_cmdline == "claudeteam"
