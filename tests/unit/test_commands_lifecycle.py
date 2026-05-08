"""Tests for `claudeteam start / hire / fire` — team lifecycle commands.

Mocks runtime.tmux entirely so tests don't need a real tmux server.  Uses
isolated_env(team=...) for the env / file fixture.
"""
from __future__ import annotations

import contextlib

from helpers import isolated_env, run_cli, tmux_patch
from claudeteam.agents import identity
from claudeteam.store import local_facts


def _isolated_team(team_data):
    return isolated_env(team=team_data)


# All ready-marker strings across every adapter. capture_pane returns this
# blob so wake.wait_until_ready short-circuits on the first poll regardless
# of which CLI the test team uses. Without it each spawn paid the 60s
# wake timeout (R172.b raised it from 20s for fresh-launch dialog headroom),
# and a 3-agent test took 180s of pure idle sleep.
_ALL_READY_MARKERS = (
    "bypass permissions on\n? for shortcuts\n"        # claude-code
    "OpenAI Codex\npermissions: YOLO\n"                # codex-cli
    "Welcome to Kimi Code CLI\nSend /help for help\n"  # kimi-code
    ">\nType your request\n"                            # gemini-cli / qwen-code
)


@contextlib.contextmanager
def _fake_tmux():
    """Recording fake for every tmux function used by start/hire/fire.

    Returns a state dict tracking:
      session_exists: set of session names has_session() reports True for
      windows:        set of "session:window" strings has_window() reports
      calls:          ordered (op, *args) trace for assertions
    """
    state = {"session_exists": set(), "windows": set(), "calls": []}

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

    def capture_pane(target, lines=80):
        return _ALL_READY_MARKERS

    def inject(t, text, *, submit_keys=("Enter",)):
        state["calls"].append(("inject", str(t), text))
        return True

    with tmux_patch(has_session=has_session, has_window=has_window,
                    new_session=new_session, new_window=new_window,
                    kill_window=kill_window, spawn_agent=spawn_agent,
                    send_keys=send_keys, capture_pane=capture_pane,
                    inject=inject):
        yield state




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


def test_start_propagates_state_dir_into_pane_env():
    """REGRESSION: round 4 smoke caught that worker_cc's \`claudeteam say\`
    wrote to ~/.claudeteam/facts/logs.jsonl instead of the project state
    dir, because tmux send-keys spawned the CLI in a fresh shell that
    didn't inherit CLAUDETEAM_STATE_DIR. Spawn line must prepend it."""
    team = {"session": "T", "agents": {"w_cc": {"cli": "claude-code"}}}
    with _isolated_team(team) as tmp, _fake_tmux() as fake:
        run_cli(["start"])
        cmd = next(c[2] for c in fake["calls"] if c[0] == "spawn_agent")
        # State dir from isolated_env points under tmp/state
        assert "CLAUDETEAM_STATE_DIR=" in cmd
        assert str(tmp / "state") in cmd
        # IS_SANDBOX=1 still there (claude-code adapter prefix)
        assert "IS_SANDBOX=1" in cmd


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
        assert identity.identity_path("new").exists()


def test_hire_lazy_agent_skips_spawn_and_marks_standby():
    team = {"session": "S",
            "agents": {"manager": {}, "lazy_w": {"cli": "claude-code", "lazy": True}}}
    with _isolated_team(team), _fake_tmux() as fake:
        fake["session_exists"].add("S")
        rc, out, _ = run_cli(["hire", "lazy_w"])
        assert rc == 0
        assert "hired (lazy)" in out
        # window created but no spawn_agent call
        assert "S:lazy_w" in fake["windows"]
        assert not [c for c in fake["calls"] if c[0] == "spawn_agent" and c[1] == "S:lazy_w"]
        snap = local_facts.get_status("lazy_w")
        assert snap["status"] == "待命"


def test_start_lazy_agent_creates_window_no_spawn():
    team = {
        "session": "T",
        "agents": {
            "manager": {"cli": "claude-code"},
            "sleeper": {"cli": "kimi-code", "lazy": True},
        },
    }
    with _isolated_team(team), _fake_tmux() as fake:
        rc, out, _ = run_cli(["start"])
        assert rc == 0
        assert "lazy-pane ready" in out
        spawn_targets = [c[1] for c in fake["calls"] if c[0] == "spawn_agent"]
        assert "T:manager" in spawn_targets
        assert "T:sleeper" not in spawn_targets
        assert local_facts.get_status("sleeper")["status"] == "待命"


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
