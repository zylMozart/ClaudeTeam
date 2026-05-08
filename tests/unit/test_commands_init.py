"""Tests for `claudeteam init` — first-time bootstrap.

Post-config-unification: writes a single `claudeteam.toml` instead of
team.json + runtime_config.json. `--upgrade` reads existing legacy
files and merges them into a toml (legacy left as backup).
"""
from __future__ import annotations

import sys

if sys.version_info >= (3, 11):
    import tomllib
else:  # pragma: no cover
    import tomli as tomllib

from helpers import env_patch, isolated_env, run_cli


def _tmp_env():
    """isolated_env points CLAUDETEAM_CONFIG_FILE at a fresh tempdir's
    claudeteam.toml that doesn't exist yet (init writes it)."""
    return isolated_env()


def _read_toml(path):
    return tomllib.loads(path.read_text(encoding="utf-8"))


# ── happy path ───────────────────────────────────────────────────


def test_init_writes_toml_and_returns_zero():
    with _tmp_env() as tmp:
        with env_patch(CLAUDETEAM_CONFIG_FILE=str(tmp / "claudeteam.toml")):
            rc, out, _ = run_cli(["init"])
        assert rc == 0
        assert (tmp / "claudeteam.toml").exists()
        assert "wrote" in out
        assert "Next:" in out


def test_init_default_team_has_three_agents():
    with _tmp_env() as tmp:
        with env_patch(CLAUDETEAM_CONFIG_FILE=str(tmp / "claudeteam.toml")):
            run_cli(["init"])
        cfg = _read_toml(tmp / "claudeteam.toml")
        agents = cfg["team"]["agents"]
        assert set(agents) == {"manager", "worker_cc", "worker_codex"}
        assert agents["manager"]["cli"] == "claude-code"
        assert agents["worker_codex"]["cli"] == "codex-cli"


def test_init_default_chat_id_and_profile_empty():
    with _tmp_env() as tmp:
        with env_patch(CLAUDETEAM_CONFIG_FILE=str(tmp / "claudeteam.toml")):
            run_cli(["init"])
        cfg = _read_toml(tmp / "claudeteam.toml")
        assert cfg["chat_id"] == ""
        assert cfg["lark_profile"] == ""


def test_init_emits_chat_publish_section():
    with _tmp_env() as tmp:
        with env_patch(CLAUDETEAM_CONFIG_FILE=str(tmp / "claudeteam.toml")):
            run_cli(["init"])
        cfg = _read_toml(tmp / "claudeteam.toml")
        assert cfg["chat"]["publish"]["user_to_manager"] == "always"
        # 2026-05-06: defaults flipped to True/always — boss-flagged
        # "测试阶段多看到一些东西" — see init.py header comment.
        assert cfg["chat"]["publish"]["manager_to_worker"] is True
        assert cfg["chat"]["publish"]["worker_to_manager"] is True
        assert cfg["chat"]["publish"]["worker_to_worker"] is True


def test_init_session_flag_overrides_default():
    with _tmp_env() as tmp:
        with env_patch(CLAUDETEAM_CONFIG_FILE=str(tmp / "claudeteam.toml")):
            run_cli(["init", "--session", "Alpha"])
        cfg = _read_toml(tmp / "claudeteam.toml")
        assert cfg["team"]["session"] == "Alpha"


# ── overwrite protection ─────────────────────────────────────────


def test_init_refuses_to_overwrite_existing_toml():
    with _tmp_env() as tmp:
        toml_path = tmp / "claudeteam.toml"
        toml_path.write_text('chat_id = "preserved"\n', encoding="utf-8")
        with env_patch(CLAUDETEAM_CONFIG_FILE=str(toml_path)):
            rc, _, err = run_cli(["init"])
        assert rc == 1
        assert "already exists" in err
        # untouched
        assert "preserved" in toml_path.read_text(encoding="utf-8")


def test_init_force_overwrites():
    with _tmp_env() as tmp:
        toml_path = tmp / "claudeteam.toml"
        toml_path.write_text('chat_id = "old"\n', encoding="utf-8")
        with env_patch(CLAUDETEAM_CONFIG_FILE=str(toml_path)):
            rc, _, _ = run_cli(["init", "--force"])
        assert rc == 0
        cfg = _read_toml(toml_path)
        assert cfg["chat_id"] == ""
        assert "manager" in cfg["team"]["agents"]


# ── --upgrade migration ──────────────────────────────────────────


def test_upgrade_merges_legacy_team_json():
    """Existing team.json + runtime_config.json → claudeteam.toml with
    the legacy team's agents, chat_id, profile preserved."""
    legacy_team = {
        "session": "OldTeam",
        "agents": {
            "boss": {"cli": "claude-code", "model": "opus", "role": "老大"},
            "alice": {"cli": "codex-cli", "model": "gpt-5.5", "role": "数据"},
        },
        "default_model": "sonnet",
    }
    legacy_runtime = {"chat_id": "oc_legacy", "lark_profile": "old-profile"}
    with isolated_env(team=legacy_team, runtime_config=legacy_runtime) as tmp:
        with env_patch(CLAUDETEAM_CONFIG_FILE=str(tmp / "claudeteam.toml")):
            rc, out, _ = run_cli(["init", "--upgrade"])
        assert rc == 0, out
        cfg = _read_toml(tmp / "claudeteam.toml")
        assert cfg["chat_id"] == "oc_legacy"
        assert cfg["lark_profile"] == "old-profile"
        assert cfg["default_model"] == "sonnet"
        assert cfg["team"]["session"] == "OldTeam"
        assert set(cfg["team"]["agents"]) == {"boss", "alice"}
        assert cfg["team"]["agents"]["alice"]["cli"] == "codex-cli"


def test_upgrade_errors_when_no_legacy_files():
    with isolated_env() as tmp:
        with env_patch(CLAUDETEAM_CONFIG_FILE=str(tmp / "claudeteam.toml")):
            rc, _, err = run_cli(["init", "--upgrade"])
        assert rc == 1
        assert "nothing to migrate" in err


def test_upgrade_emits_legacy_preserved_note():
    legacy_team = {"session": "X", "agents": {"a": {"cli": "claude-code"}}}
    legacy_runtime = {"chat_id": "oc_x"}
    with isolated_env(team=legacy_team, runtime_config=legacy_runtime) as tmp:
        with env_patch(CLAUDETEAM_CONFIG_FILE=str(tmp / "claudeteam.toml")):
            rc, out, _ = run_cli(["init", "--upgrade"])
        assert rc == 0
        assert "preserved as backup" in out


# ── help / arg validation ────────────────────────────────────────


def test_init_help_returns_zero():
    rc, out, _ = run_cli(["init", "--help"])
    assert rc == 0
    assert "usage: claudeteam init" in out


def test_init_unknown_arg_returns_one():
    with _tmp_env():
        rc, _, err = run_cli(["init", "--bogus"])
        assert rc == 1
        assert "unexpected args" in err


# ── template self-check ──────────────────────────────────────────


def test_init_template_passes_tomllib_parse():
    """The string template is hand-written; this is a sanity check it
    actually parses via stdlib tomllib (catches typos at test time
    before they break production deploys)."""
    with _tmp_env() as tmp:
        with env_patch(CLAUDETEAM_CONFIG_FILE=str(tmp / "claudeteam.toml")):
            run_cli(["init"])
        # Just parsing without raising = passes
        cfg = _read_toml(tmp / "claudeteam.toml")
        # Spot-check a few required keys are present
        assert "chat_id" in cfg
        assert "team" in cfg
        assert "chat" in cfg
        assert "limits" in cfg
        assert "router" in cfg
        assert "feishu" in cfg
