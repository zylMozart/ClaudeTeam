"""Tests for runtime/watchdog.py — process supervision state machine."""
from __future__ import annotations

from pathlib import Path

from claudeteam.runtime.watchdog import (
    ProcessSpec,
    ProcessState,
    is_alive,
    respawn,
    supervise,
)


def _spec(**overrides) -> ProcessSpec:
    base = dict(
        name="router",
        pid_file=Path("/tmp/test.pid"),
        expected_cmdline="claudeteam router",
        spawn_cmd=["claudeteam", "router"],
        max_retries=3,
        cooldown_secs=600,
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
    from helpers import isolated_env
    from claudeteam.runtime.watchdog import default_specs
    with isolated_env() as tmp:
        specs = default_specs()
        assert any(s.name == "router" for s in specs)
        router = next(s for s in specs if s.name == "router")
        assert str(router.pid_file).startswith(str(tmp))
        assert router.spawn_cmd == ["claudeteam", "router"]
