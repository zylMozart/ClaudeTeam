"""Tests for agents/identity.py — per-agent identity markdown rendering."""
from __future__ import annotations

from helpers import isolated_env
from claudeteam.agents import identity
from claudeteam.store import memory


# ── render() — template selection ─────────────────────────────────


def test_render_manager_uses_manager_template():
    """Round-85: manager identity rewritten in Chinese with reference/main's
    rich management discipline (角色边界 / 秒回闭环 / 巡视核实 / 集合指令铁律)."""
    text = identity.render("manager", role="团队主管",
                           cli="claude-code", model="opus")
    assert "团队主管" in text
    assert "manager" in text
    # Core management rules from main's manager.identity.md
    assert "管理分发铁律" in text
    assert "集合类指令必须 dispatch" in text
    # Argument-order contract carried over from rebuild's earlier version
    assert "claudeteam send <recipient> <sender>" in text


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


def test_render_warns_against_cd_in_both_templates():
    """REGRESSION: round 5 smoke caught worker_cc prefixing \`cd /repo &&\`
    on its first reply attempt, which broke chat_id resolution. Both
    templates must include an explicit "do not cd" rule."""
    for agent in ("manager", "w"):
        text = identity.render(agent, role="r", cli="c", model="m")
        assert "Working directory rule" in text
        assert "do NOT" in text and "cd" in text
        assert "runtime_config.json" in text


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
    team = {"agents": {"manager": {"cli": "claude-code", "model": "opus",
                                    "role": "团队主管"}}}
    with isolated_env(team=team) as tmp:
        path = identity.write("manager")
        assert path.exists()
        assert path == tmp / "state" / "agents" / "manager" / "identity.md"
        text = path.read_text(encoding="utf-8")
        # Round-85: manager body now in Chinese, anchored on "管理分发铁律"
        assert "团队主管" in text
        assert "管理分发铁律" in text


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


# ── init_prompt() — round-84 memory injection ─────────────────────


def test_init_prompt_omits_memory_section_when_empty():
    """Brand-new agent: no memory file, no extra section appended.
    Avoids confusing the agent with a `## 既往记忆` block that's empty."""
    with isolated_env():
        prompt = identity.init_prompt("manager")
        assert "claudeteam inbox manager" in prompt
        assert "既往记忆" not in prompt


def test_init_prompt_appends_memory_when_present():
    """After memory.append, the next init_prompt should include the
    memory block so a /clear-ed pane re-reads its prior context on wake."""
    with isolated_env():
        memory.append("manager", "task_assigned", "fix login bug", ref="om_1")
        memory.append("manager", "learning", "auth uses bcrypt")
        prompt = identity.init_prompt("manager")
        # Base reporting still present
        assert "claudeteam inbox manager" in prompt
        # Memory block present
        assert "## 既往记忆" in prompt
        assert "[task_assigned] fix login bug (ref=om_1)" in prompt
        assert "[learning] auth uses bcrypt" in prompt
        # Tail nudge tells agent what to do with the recall
        assert "继续之前未完成的工作" in prompt
