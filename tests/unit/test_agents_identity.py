"""Tests for agents/identity.py — per-agent identity markdown rendering."""
from __future__ import annotations

from helpers import isolated_env
from claudeteam.agents import identity


# ── render() — template selection ─────────────────────────────────


def test_render_manager_uses_manager_template():
    text = identity.render("manager", role="team manager",
                           cli="claude-code", model="opus")
    assert "team manager" in text
    assert "manager" in text
    assert "Receive messages from the boss" in text


def test_render_worker_uses_worker_template():
    text = identity.render("worker_cc", role="frontend",
                           cli="claude-code", model="sonnet")
    assert "team worker" in text
    assert "Pick up tasks" in text


# ── render() — substitutions ──────────────────────────────────────


def test_render_substitutes_name_role_cli_model():
    text = identity.render("worker_codex", role="backend",
                           cli="codex-cli", model="gpt-5.5")
    assert "worker_codex" in text
    assert "backend" in text
    assert "codex-cli" in text
    assert "gpt-5.5" in text


def test_render_argument_order_contract_present_in_manager():
    text = identity.render("manager", role="r", cli="c", model="m")
    assert "claudeteam send <recipient> <sender>" in text
    assert "claudeteam say <agent>" in text
    assert "❌" in text and "✅" in text


def test_render_argument_order_contract_present_in_worker():
    text = identity.render("w", role="r", cli="c", model="m")
    assert "claudeteam send <recipient> <sender>" in text
    assert "claudeteam say <agent>" in text
    assert "❌" in text and "✅" in text


# ── render() — defaults from team.json ────────────────────────────


def test_render_pulls_defaults_from_team_json_when_args_omitted():
    team = {"agents": {"manager": {"cli": "claude-code", "model": "opus",
                                   "role": "captain"}}}
    with isolated_env(team=team):
        text = identity.render("manager")
    assert "captain" in text
    assert "claude-code" in text
    assert "opus" in text


def test_render_falls_back_when_team_json_missing_fields():
    team = {"agents": {"w": {}}}
    with isolated_env(team=team):
        text = identity.render("w")
    # name is the agent name; cli defaults to claude-code; model empty
    assert "**w**" in text
    assert "claude-code" in text


# ── identity_path() / write() ─────────────────────────────────────


def test_identity_path_under_state_dir():
    with isolated_env() as tmp:
        p = identity.identity_path("worker_kimi")
        assert p == tmp / "state" / "agents" / "worker_kimi" / "identity.md"


def test_write_persists_file_and_creates_parents():
    team = {"agents": {"manager": {"cli": "claude-code", "model": "opus"}}}
    with isolated_env(team=team) as tmp:
        path = identity.write("manager")
        assert path.exists()
        assert path == tmp / "state" / "agents" / "manager" / "identity.md"
        text = path.read_text(encoding="utf-8")
        assert "team manager" in text


def test_write_overwrites_existing_file():
    team = {"agents": {"w": {"cli": "claude-code", "model": "opus", "role": "old"}}}
    with isolated_env(team=team):
        path = identity.write("w")
        first = path.read_text(encoding="utf-8")
        assert "old" in first
        # render again with overrides
        identity.write("w", role="new")
        second = path.read_text(encoding="utf-8")
        assert "new" in second
        assert "old" not in second
