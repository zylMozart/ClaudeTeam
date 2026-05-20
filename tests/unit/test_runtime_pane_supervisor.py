"""Tests for runtime/pane_supervisor.py — sweep + _pane_alive.

New module from commit a7bf910; CLAUDE.md rule: every new module ships
its own unit test in the same commit.
"""
from __future__ import annotations

from helpers import isolated_env
from claudeteam.runtime import pane_supervisor, tmux
from claudeteam.runtime.pane_supervisor import _pane_alive, sweep


# ── _pane_alive ───────────────────────────────────────────────────────


def _fake_adapter(ready=(), busy=(), rate_limit=()):
    """Minimal adapter stub with configurable marker lists."""
    class _A:
        def ready_markers(self): return list(ready)
        def busy_markers(self): return list(busy)
        def rate_limit_markers(self): return list(rate_limit)
    return _A()


def test_pane_alive_false_when_no_window():
    target = tmux.Target("S", "a")
    adapter = _fake_adapter(ready=["? for shortcuts"])
    result = _pane_alive(
        target, adapter,
        has_window=lambda t: False,
        capture=lambda t, **kw: "? for shortcuts",
    )
    assert result is False


def test_pane_alive_true_on_ready_marker():
    target = tmux.Target("S", "a")
    adapter = _fake_adapter(ready=["? for shortcuts"])
    result = _pane_alive(
        target, adapter,
        has_window=lambda t: True,
        capture=lambda t, **kw: "some text ? for shortcuts more text",
    )
    assert result is True


def test_pane_alive_true_on_busy_marker():
    target = tmux.Target("S", "a")
    adapter = _fake_adapter(busy=["Thinking"])
    result = _pane_alive(
        target, adapter,
        has_window=lambda t: True,
        capture=lambda t, **kw: "Thinking...",
    )
    assert result is True


def test_pane_alive_true_on_rate_limit_marker():
    target = tmux.Target("S", "a")
    adapter = _fake_adapter(rate_limit=["Try again at"])
    result = _pane_alive(
        target, adapter,
        has_window=lambda t: True,
        capture=lambda t, **kw: "Try again at 14:00",
    )
    assert result is True


def test_pane_alive_false_when_no_markers_match():
    target = tmux.Target("S", "a")
    adapter = _fake_adapter(ready=["? for shortcuts"], busy=["Thinking"])
    result = _pane_alive(
        target, adapter,
        has_window=lambda t: True,
        capture=lambda t, **kw: "some random output",
    )
    assert result is False


# ── sweep: basic counting ─────────────────────────────────────────────


def _make_sweep_kwargs(team, *, statuses=None, alive_agents=None):
    """Build injectable kwargs for sweep() with no real tmux/store calls."""
    statuses = statuses or {}
    alive_agents = alive_agents or set()
    flipped = {}

    def get_status(agent):
        return statuses.get(agent)

    def upsert_status(agent, status):
        flipped[agent] = status

    def has_window(target):
        return target.window in alive_agents

    def capture(target, **kw):
        # Return a ready marker so _pane_alive returns True for alive agents
        return "? for shortcuts" if target.window in alive_agents else ""

    return dict(
        has_window=has_window,
        capture=capture,
        get_status=get_status,
        upsert_status=upsert_status,
        Load_team=lambda: team,
        session_name=lambda: "TestSession",
        adapter_for=lambda cli: _fake_adapter(ready=["? for shortcuts"]),
        log=lambda *a: None,
    ), flipped


def test_sweep_returns_zero_when_all_panes_alive():
    team = {"agents": {"manager": {"cli": "claude-code"}, "worker": {"cli": "claude-code"}}}
    kwargs, flipped = _make_sweep_kwargs(team, alive_agents={"manager", "worker"})
    with isolated_env(team=team):
        result = sweep(**kwargs)
    assert result == 0
    assert flipped == {}


def test_sweep_flips_dead_pane_to_pane_closed():
    team = {"agents": {"manager": {"cli": "claude-code"}, "worker": {"cli": "claude-code"}}}
    # manager alive, worker dead
    kwargs, flipped = _make_sweep_kwargs(team, alive_agents={"manager"})
    with isolated_env(team=team):
        result = sweep(**kwargs)
    assert result == 1
    assert flipped == {"worker": "pane_closed"}


def test_sweep_flips_multiple_dead_panes():
    team = {"agents": {
        "a": {"cli": "claude-code"},
        "b": {"cli": "claude-code"},
        "c": {"cli": "claude-code"},
    }}
    kwargs, flipped = _make_sweep_kwargs(team, alive_agents=set())
    with isolated_env(team=team):
        result = sweep(**kwargs)
    assert result == 3
    assert set(flipped) == {"a", "b", "c"}
    assert all(v == "pane_closed" for v in flipped.values())


# ── sweep: skip rules ─────────────────────────────────────────────────


def test_sweep_skips_lazy_agents():
    team = {"agents": {"lazy_one": {"cli": "claude-code", "lazy": True}}}
    kwargs, flipped = _make_sweep_kwargs(team, alive_agents=set())
    with isolated_env(team=team):
        result = sweep(**kwargs)
    assert result == 0
    assert flipped == {}


def test_sweep_skips_agent_with_待命_status():
    team = {"agents": {"sleepy": {"cli": "claude-code"}}}
    kwargs, flipped = _make_sweep_kwargs(
        team,
        statuses={"sleepy": {"status": "待命"}},
        alive_agents=set(),
    )
    with isolated_env(team=team):
        result = sweep(**kwargs)
    assert result == 0
    assert flipped == {}


def test_sweep_skips_agent_with_已退出_status():
    team = {"agents": {"gone": {"cli": "claude-code"}}}
    kwargs, flipped = _make_sweep_kwargs(
        team,
        statuses={"gone": {"status": "已退出"}},
        alive_agents=set(),
    )
    with isolated_env(team=team):
        result = sweep(**kwargs)
    assert result == 0
    assert flipped == {}


def test_sweep_skips_unknown_cli_without_raising():
    """KeyError from adapter_for must be swallowed; other agents still processed."""
    team = {"agents": {
        "bad": {"cli": "nonexistent-cli"},
        "good": {"cli": "claude-code"},
    }}

    def adapter_for(cli):
        if cli == "nonexistent-cli":
            raise KeyError(cli)
        return _fake_adapter(ready=["? for shortcuts"])

    kwargs, flipped = _make_sweep_kwargs(team, alive_agents=set())
    kwargs["adapter_for"] = adapter_for
    with isolated_env(team=team):
        result = sweep(**kwargs)
    # only "good" is flipped; "bad" was skipped
    assert result == 1
    assert "good" in flipped
    assert "bad" not in flipped


def test_sweep_empty_team_returns_zero():
    team = {"agents": {}}
    kwargs, flipped = _make_sweep_kwargs(team)
    with isolated_env(team=team):
        result = sweep(**kwargs)
    assert result == 0


# ── pane_env_prefix: CLAUDETEAM_AGENT_NAME (new in this commit) ───────


def test_pane_env_prefix_includes_agent_name():
    """pane_env_prefix(agent) must now embed CLAUDETEAM_AGENT_NAME so the
    status hook script can identify which agent it belongs to."""
    from claudeteam.runtime.lifecycle import pane_env_prefix
    with isolated_env(team={"agents": {"worker_cc": {}}}):
        prefix = pane_env_prefix("worker_cc")
    assert "CLAUDETEAM_AGENT_NAME=worker_cc" in prefix


def test_pane_env_prefix_quotes_agent_name_with_special_chars():
    """Agent names with hyphens/underscores must survive shlex.quote."""
    from claudeteam.runtime.lifecycle import pane_env_prefix
    with isolated_env(team={"agents": {"worker-01": {}}}):
        prefix = pane_env_prefix("worker-01")
    assert "CLAUDETEAM_AGENT_NAME=" in prefix
    assert "worker-01" in prefix
