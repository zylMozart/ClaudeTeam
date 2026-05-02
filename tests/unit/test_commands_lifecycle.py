"""Tests for `claudeteam start / hire / fire` — team lifecycle commands.

Mocks runtime.tmux entirely so tests don't need a real tmux server.  Uses
isolated_env(team=...) for the env / file fixture.
"""
from __future__ import annotations

import contextlib

from helpers import isolated_env, run_cli
from claudeteam.runtime import tmux
from claudeteam.store import local_facts


def _isolated_team(team_data):
    return isolated_env(team=team_data)


@contextlib.contextmanager
def _fake_tmux():
    """Replace every tmux function with a recording fake.

    Returns a dict of:
      session_exists: set of session names where has_session() returns True
      windows: set of "session:window" strings where has_window returns True
      calls: list of (op, *args) recorded across all functions
    """
    state = {
        "session_exists": set(),
        "windows": set(),
        "calls": [],
    }

    def has_session(s):
        state["calls"].append(("has_session", s))
        return s in state["session_exists"]

    def has_window(t):
        state["calls"].append(("has_window", str(t)))
        return str(t) in state["windows"]

    def new_session(s, *, window="manager", detached=True):
        state["calls"].append(("new_session", s, window))
        state["session_exists"].add(s)
        state["windows"].add(f"{s}:{window}")
        return True

    def new_window(t):
        state["calls"].append(("new_window", str(t)))
        state["windows"].add(str(t))
        return True

    def kill_window(t):
        state["calls"].append(("kill_window", str(t)))
        state["windows"].discard(str(t))
        return True

    def spawn_agent(t, cmd):
        state["calls"].append(("spawn_agent", str(t), cmd))
        return True

    def send_keys(t, *keys):
        state["calls"].append(("send_keys", str(t), *keys))
        return True

    originals = {
        "has_session": tmux.has_session,
        "has_window": tmux.has_window,
        "new_session": tmux.new_session,
        "new_window": tmux.new_window,
        "kill_window": tmux.kill_window,
        "spawn_agent": tmux.spawn_agent,
        "send_keys": tmux.send_keys,
    }
    tmux.has_session = has_session
    tmux.has_window = has_window
    tmux.new_session = new_session
    tmux.new_window = new_window
    tmux.kill_window = kill_window
    tmux.spawn_agent = spawn_agent
    tmux.send_keys = send_keys
    try:
        yield state
    finally:
        for name, fn in originals.items():
            setattr(tmux, name, fn)




# ── start ──────────────────────────────────────────────────────────


def test_start_creates_session_and_one_window_per_agent():
    team = {
        "session": "MyTeam",
        "agents": {
            "manager":      {"cli": "claude-code", "model": "opus"},
            "worker_codex": {"cli": "codex-cli",   "model": "gpt-5.5"},
            "worker_kimi":  {"cli": "kimi-code"},
        },
    }
    with _isolated_team(team), _fake_tmux() as fake:
        rc, out, _ = run_cli(["start"])
        assert rc == 0, out
        assert "🚀 created tmux session MyTeam" in out
        assert "✅ team MyTeam started (3 agents)" in out

        # session created with first agent (manager) as the initial window
        new_sessions = [c for c in fake["calls"] if c[0] == "new_session"]
        assert new_sessions == [("new_session", "MyTeam", "manager")]

        # the other two get new_window calls
        new_windows = [c for c in fake["calls"] if c[0] == "new_window"]
        assert sorted(c[1] for c in new_windows) == ["MyTeam:worker_codex", "MyTeam:worker_kimi"]

        # all three got a spawn_agent call
        spawned = {c[1] for c in fake["calls"] if c[0] == "spawn_agent"}
        assert spawned == {"MyTeam:manager", "MyTeam:worker_codex", "MyTeam:worker_kimi"}

        # status uppserted for each
        for agent in ("manager", "worker_codex", "worker_kimi"):
            snap = local_facts.get_status(agent)
            assert snap is not None
            assert snap["status"] == "进行中"

        # each agent gets an identity.md
        from claudeteam.agents import identity
        for agent in ("manager", "worker_codex", "worker_kimi"):
            assert identity.identity_path(agent).exists()


def test_start_refuses_when_session_already_running():
    team = {"session": "S", "agents": {"manager": {}}}
    with _isolated_team(team), _fake_tmux() as fake:
        fake["session_exists"].add("S")
        rc, out, _ = run_cli(["start"])
        assert rc == 1
        assert "already running" in out


def test_start_with_no_agents_returns_one():
    team = {"session": "S", "agents": {}}
    with _isolated_team(team), _fake_tmux():
        rc, _, err = run_cli(["start"])
        assert rc == 1
        assert "no agents" in err


def test_start_picks_correct_spawn_cmd_per_cli():
    team = {
        "session": "T",
        "agents": {
            "w_cc":    {"cli": "claude-code", "model": "sonnet"},
            "w_codex": {"cli": "codex-cli",   "model": "gpt-5.5"},
        },
    }
    with _isolated_team(team), _fake_tmux() as fake:
        run_cli(["start"])
        spawn_cmds = {c[1]: c[2] for c in fake["calls"] if c[0] == "spawn_agent"}
        assert "claude --dangerously-skip-permissions" in spawn_cmds["T:w_cc"]
        assert "codex" in spawn_cmds["T:w_codex"]
        assert "--model gpt-5.5" in spawn_cmds["T:w_codex"]


# ── hire ──────────────────────────────────────────────────────────


def test_hire_unknown_agent_returns_one():
    team = {"session": "S", "agents": {"manager": {}}}
    with _isolated_team(team), _fake_tmux() as fake:
        fake["session_exists"].add("S")
        rc, _, err = run_cli(["hire", "ghost"])
        assert rc == 1
        assert "unknown agent" in err


def test_hire_when_session_not_running_returns_one():
    team = {"session": "S", "agents": {"new_worker": {"cli": "claude-code"}}}
    with _isolated_team(team), _fake_tmux():
        rc, _, err = run_cli(["hire", "new_worker"])
        assert rc == 1
        assert "not running" in err


def test_hire_creates_window_spawns_and_writes_status():
    team = {"session": "S", "agents": {"manager": {}, "new": {"cli": "kimi-code"}}}
    with _isolated_team(team), _fake_tmux() as fake:
        fake["session_exists"].add("S")
        rc, out, _ = run_cli(["hire", "new"])
        assert rc == 0, out
        assert "hired: new" in out
        assert "S:new" in fake["windows"]
        assert local_facts.get_status("new")["status"] == "进行中"
        # identity.md should now exist for the hired agent
        from claudeteam.agents import identity
        assert identity.identity_path("new").exists()


def test_hire_when_window_already_exists_is_idempotent():
    team = {"session": "S", "agents": {"manager": {}, "x": {}}}
    with _isolated_team(team), _fake_tmux() as fake:
        fake["session_exists"].add("S")
        fake["windows"].add("S:x")
        rc, out, _ = run_cli(["hire", "x"])
        assert rc == 0
        assert "already has a pane" in out


# ── fire ──────────────────────────────────────────────────────────


def test_fire_unknown_pane_marks_status_only():
    team = {"session": "S", "agents": {"manager": {}, "x": {}}}
    with _isolated_team(team), _fake_tmux() as fake:
        fake["session_exists"].add("S")
        rc, out, _ = run_cli(["fire", "x"])
        assert rc == 0
        assert "no pane in session" in out
        assert local_facts.get_status("x")["status"] == "已停止"


def test_fire_existing_pane_sends_ctrl_c_and_kills_window():
    team = {"session": "S", "agents": {"manager": {}, "x": {}}}
    with _isolated_team(team), _fake_tmux() as fake:
        fake["session_exists"].add("S")
        fake["windows"].add("S:x")
        rc, out, _ = run_cli(["fire", "x"])
        assert rc == 0
        assert "fired: x" in out
        # ctrl-c sent before kill
        ops = [c for c in fake["calls"] if c[0] in ("send_keys", "kill_window")]
        assert ops[0] == ("send_keys", "S:x", "C-c")
        assert ("kill_window", "S:x") in fake["calls"]
        assert "S:x" not in fake["windows"]
        assert local_facts.get_status("x")["status"] == "已停止"


def test_fire_refuses_to_fire_manager():
    with _fake_tmux():
        rc, _, err = run_cli(["fire", "manager"])
        assert rc == 1
        assert "refusing to fire manager" in err


def test_fire_zero_args_returns_one():
    rc, _, err = run_cli(["fire"])
    assert rc == 1
    assert "usage:" in err
