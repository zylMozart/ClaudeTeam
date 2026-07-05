"""Tests for `claudeteam start / hire / fire` — team lifecycle commands.

Mocks runtime.tmux entirely so tests don't need a real tmux server.  Uses
isolated_env(team=...) for the env / file fixture.
"""
from __future__ import annotations

import contextlib
import time

from helpers import attr_patch, isolated_env, run_cli, tmux_patch
from claudeteam.agents import identity
from claudeteam.store import local_facts


def _isolated_team(team_data):
    return isolated_env(team=team_data)


# All ready-marker strings across every adapter. capture_pane returns this
# blob so wake.wait_until_ready short-circuits on the first poll regardless
# of which CLI the test team uses. Without it each spawn paid the 60s
# wake timeout (raised from 20s for fresh-launch dialog headroom),
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

    # Always carries every adapter's ready markers (so wait_until_ready
    # short-circuits on the first poll) AND changes each call, so provision's
    # motion-based inject_and_confirm sees the pane move = submitted and
    # returns without escalating the submit key.
    def capture_pane(target, lines=80):
        state["cap_n"] = state.get("cap_n", 0) + 1
        return _ALL_READY_MARKERS + f"\nframe {state['cap_n']}\n"

    def inject(t, text, *, submit_keys=("Enter",)):
        state["calls"].append(("inject", str(t), text))
        return True

    # No-op sleep: provision's inject_and_confirm always settles once before
    # checking for motion; without this each provisioned pane paid ~1s.
    with tmux_patch(available=lambda: True,
                    has_session=has_session, has_window=has_window,
                    new_session=new_session, new_window=new_window,
                    kill_window=kill_window, spawn_agent=spawn_agent,
                    send_keys=send_keys, capture_pane=capture_pane,
                    inject=inject), \
         attr_patch(time, sleep=lambda *a, **k: None):
        yield state




# ── start ──────────────────────────────────────────────────────────


def test_start_creates_session_and_one_window_per_agent():
    team = {
        "session": "MyTeam",
        "agents": {
            "manager":      {"cli": "claude-code", "model": "opus", "runner": "tmux"},
            "worker_codex": {"cli": "codex-cli",   "model": "gpt-5.5", "runner": "tmux"},
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
            "w_cc":    {"cli": "claude-code", "model": "sonnet", "runner": "tmux"},
            "w_codex": {"cli": "codex-cli",   "model": "gpt-5.5", "runner": "tmux"},
        },
    }
    with _isolated_team(team), _fake_tmux() as fake:
        run_cli(["start"])
        spawn_cmds = {c[1]: c[2] for c in fake["calls"] if c[0] == "spawn_agent"}
        assert "claude --dangerously-skip-permissions" in spawn_cmds["T:w_cc"]
        assert "codex" in spawn_cmds["T:w_codex"]
        assert "--model gpt-5.5" in spawn_cmds["T:w_codex"]


def test_start_propagates_state_dir_into_pane_env():
    """REGRESSION: worker_cc's \`claudeteam say\` wrote to
    ~/.claudeteam/facts/logs.jsonl instead of the project state dir,
    because tmux send-keys spawned the CLI in a fresh shell that didn't
    inherit CLAUDETEAM_STATE_DIR. The pane must SOURCE an env file that
    sets it — not an inline KEY=value prefix, which leaked secrets into
    the scrollback + the agent's context."""
    team = {"session": "T", "agents": {"w_cc": {"cli": "claude-code", "runner": "tmux"}}}
    with _isolated_team(team) as tmp, _fake_tmux() as fake:
        run_cli(["start"])
        cmd = next(c[2] for c in fake["calls"] if c[0] == "spawn_agent")
        # env is sourced from a private file, not typed in as KEY=value
        assert "CLAUDETEAM_STATE_DIR=" not in cmd
        envfile = tmp / "state" / "spawn-env" / "w_cc.sh"
        assert cmd.startswith(". ") and str(envfile) in cmd
        body = envfile.read_text()
        assert "CLAUDETEAM_STATE_DIR=" in body
        assert str(tmp / "state") in body
        # IS_SANDBOX=1 still there (claude-code adapter's own spawn_cmd)
        assert "IS_SANDBOX=1" in cmd


# ── hire ──────────────────────────────────────────────────────────


def test_hire_unknown_agent_returns_one():
    """Not in roster AND no archive to restore → can't hire."""
    team = {"session": "S", "agents": {"manager": {}}}
    with _isolated_team(team), _fake_tmux() as fake:
        fake["session_exists"].add("S")
        rc, _, err = run_cli(["hire", "ghost"])
        assert rc == 1
        assert "cannot hire ghost" in err


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
            "agents": {"manager": {}, "lazy_w": {"cli": "claude-code", "lazy": True, "runner": "tmux"}}}
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


def test_hire_when_window_already_exists_is_idempotent():
    team = {"session": "S", "agents": {"manager": {}, "x": {}}}
    with _isolated_team(team), _fake_tmux() as fake:
        fake["session_exists"].add("S")
        fake["windows"].add("S:x")
        rc, out, _ = run_cli(["hire", "x"])
        assert rc == 0
        assert "already has a pane" in out


# ── fire ──────────────────────────────────────────────────────────


def test_fire_no_pane_still_archives_and_removes_from_roster():
    """fire is destructive 裁员 even with no live pane: status 已停止 +
    workspace archived + roster entry deleted."""
    import json
    team = {"session": "S", "agents": {"manager": {}, "x": {"cli": "claude-code"}}}
    with _isolated_team(team) as tmp, _fake_tmux() as fake:
        fake["session_exists"].add("S")
        rc, out, _ = run_cli(["fire", "x"])
        assert rc == 0
        assert "has no live pane" in out
        assert local_facts.get_status("x")["status"] == "已停止"
        # roster entry deleted (root fix: start/up can't revive it)
        roster = json.loads((tmp / "team.json").read_text())
        assert "x" not in roster["agents"] and "manager" in roster["agents"]


def test_fire_existing_pane_kills_archives_and_removes():
    """Full destructive path: Ctrl-C + kill pane, archive workspace dir
    → _archived/, drop roster entry, tombstone status 已停止."""
    import json
    from claudeteam.runtime import paths, archive
    team = {"session": "S", "agents": {"manager": {}, "x": {"cli": "claude-code"}}}
    with _isolated_team(team) as tmp, _fake_tmux() as fake:
        fake["session_exists"].add("S")
        fake["windows"].add("S:x")
        # give x a workspace dir with a file so we can prove it was archived
        wsdir = paths.agent_dir("x")
        wsdir.mkdir(parents=True, exist_ok=True)
        (wsdir / "identity.md").write_text("I am x", encoding="utf-8")

        rc, out, _ = run_cli(["fire", "x"])
        assert rc == 0
        assert "fired: x" in out
        # ctrl-c sent before kill
        ops = [c for c in fake["calls"] if c[0] in ("send_keys", "kill_window")]
        assert ops[0] == ("send_keys", "S:x", "C-c")
        assert ("kill_window", "S:x") in fake["calls"]
        assert "S:x" not in fake["windows"]
        # status tombstone
        assert local_facts.get_status("x")["status"] == "已停止"
        # roster removal
        roster = json.loads((tmp / "team.json").read_text())
        assert "x" not in roster["agents"]
        # workspace archived: original gone, archive holds the file + records
        assert not wsdir.exists()
        arc = archive.find_archived("x")
        assert arc is not None
        assert (arc / "identity.md").read_text() == "I am x"
        assert (arc / "_termination.md").exists()
        assert (arc / "_roster.json").exists()


def test_fire_twice_does_not_shadow_good_archive():
    """Re-firing an already-fired agent (gone from roster, no workspace)
    must NOT drop a fresh stash-less archive that shadows the original —
    a later `hire` must still find the original _roster.json."""
    from claudeteam.runtime import paths, archive
    team = {"session": "S", "agents": {"manager": {}, "x": {"cli": "claude-code", "model": "opus"}}}
    with _isolated_team(team), _fake_tmux() as fake:
        fake["session_exists"].add("S")
        paths.agent_dir("x").mkdir(parents=True, exist_ok=True)
        run_cli(["fire", "x"])                       # 1st fire: archives with stash
        first = archive.find_archived("x")
        assert archive.read_roster_stash(first).get("model") == "opus"

        rc, out, _ = run_cli(["fire", "x"])          # 2nd fire: nothing to archive
        assert rc == 0
        assert "nothing to archive" in out
        # the good archive (with the opus stash) is still what hire would find
        assert archive.read_roster_stash(archive.find_archived("x")).get("model") == "opus"


def test_fire_refuses_to_fire_manager():
    with _fake_tmux():
        rc, _, err = run_cli(["fire", "manager"])
        assert rc == 1
        assert "refusing to fire manager" in err


def test_fire_zero_args_returns_one():
    rc, _, err = run_cli(["fire"])
    assert rc == 1
    assert "usage:" in err


# ── retirement gate: start skips fired agents ───────────────────────


def test_start_skips_fired_agent():
    """A fired agent (status 已停止) is NOT re-provisioned by `start` — the
    core 裁员不彻底 fix. The rest of the team still comes up."""
    team = {
        "session": "S",
        "agents": {"manager": {"cli": "claude-code"},
                   "worker_fired": {"cli": "claude-code"}},
    }
    with _isolated_team(team), _fake_tmux() as fake:
        local_facts.upsert_status("worker_fired", local_facts.RETIRED_STATUS, "fired")
        rc, out, _ = run_cli(["start"])
        assert rc == 0, out
        # worker_fired was skipped; only manager got a CLI spawn
        spawned = {c[1] for c in fake["calls"] if c[0] == "spawn_agent"}
        assert "S:worker_fired" not in spawned
        assert "S:manager" in spawned
        assert "worker_fired 已停止 (fired); skipping" in out
        assert "(1 agents) (1 fired, skipped)" in out
        # its retired row is untouched (still recoverable via hire)
        assert local_facts.get_status("worker_fired")["status"] == "已停止"


# ── restart: non-destructive pane rebuild (model-switch path) ───────


def test_restart_rebuilds_pane_without_archiving_or_removing():
    """restart kills + re-provisions from the roster — NO archive, NO
    roster removal, NO 已停止. The safe model-switch / restart path."""
    import json
    from claudeteam.runtime import paths, archive
    team = {"session": "S", "agents": {"manager": {}, "x": {"cli": "claude-code", "runner": "tmux"}}}
    with _isolated_team(team) as tmp, _fake_tmux() as fake:
        fake["session_exists"].add("S")
        fake["windows"].add("S:x")
        wsdir = paths.agent_dir("x")
        wsdir.mkdir(parents=True, exist_ok=True)
        (wsdir / "memory.jsonl").write_text("m", encoding="utf-8")

        rc, out, _ = run_cli(["restart", "x"])
        assert rc == 0, out
        assert "restarted: x" in out
        # old pane killed then re-created + CLI re-spawned
        assert ("kill_window", "S:x") in fake["calls"]
        assert ("new_window", "S:x") in fake["calls"]
        assert "S:x" in {c[1] for c in fake["calls"] if c[0] == "spawn_agent"}
        # NON-destructive: roster intact, no archive, status not 已停止
        roster = json.loads((tmp / "team.json").read_text())
        assert "x" in roster["agents"]
        assert archive.find_archived("x") is None
        assert wsdir.exists()
        assert local_facts.get_status("x")["status"] == "进行中"


def test_restart_unknown_agent_returns_one():
    team = {"session": "S", "agents": {"manager": {}}}
    with _isolated_team(team), _fake_tmux() as fake:
        fake["session_exists"].add("S")
        rc, _, err = run_cli(["restart", "ghost"])
        assert rc == 1
        assert "unknown agent" in err


def test_restart_when_session_not_running_returns_one():
    team = {"session": "S", "agents": {"x": {"cli": "claude-code"}}}
    with _isolated_team(team), _fake_tmux():
        rc, _, err = run_cli(["restart", "x"])
        assert rc == 1
        assert "not running" in err


# ── hire: rehire a fired agent from its archive ─────────────────────


def test_hire_restores_fired_agent_from_archive():
    """fire → hire round-trip: hire re-adds the roster entry from the
    archived _roster.json + moves the workspace back, then provisions."""
    import json
    from claudeteam.runtime import paths, archive
    team = {"session": "S", "agents": {"manager": {}, "x": {"cli": "claude-code", "model": "opus", "runner": "tmux"}}}
    with _isolated_team(team) as tmp, _fake_tmux() as fake:
        fake["session_exists"].add("S")
        fake["windows"].add("S:x")
        wsdir = paths.agent_dir("x")
        wsdir.mkdir(parents=True, exist_ok=True)
        (wsdir / "memory.jsonl").write_text("remembered", encoding="utf-8")

        # fire removes x from roster + archives it
        run_cli(["fire", "x"])
        roster = json.loads((tmp / "team.json").read_text())
        assert "x" not in roster["agents"]
        assert not wsdir.exists()

        # hire x: no roster entry, but an archive exists → restore
        rc, out, _ = run_cli(["hire", "x"])
        assert rc == 0, out
        assert "rehired from archive" in out
        # roster entry restored with its original cfg
        roster = json.loads((tmp / "team.json").read_text())
        assert roster["agents"]["x"]["model"] == "opus"
        # workspace moved back, memory intact
        assert (wsdir / "memory.jsonl").read_text() == "remembered"
        # provisioned (status live again)
        assert local_facts.get_status("x")["status"] == "进行中"
