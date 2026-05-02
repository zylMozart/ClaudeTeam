"""Tests for `claudeteam init` — first-time bootstrap."""
from __future__ import annotations

import json

from helpers import env_patch, isolated_env, run_cli


def _tmp_env():
    """init only cares about team.json + runtime_config.json paths;
    isolated_env (no team= / runtime_config= args) already gives a fresh
    tempdir with those env vars pointing at non-existent files."""
    return isolated_env()


def test_init_writes_both_files_and_returns_zero():
    with _tmp_env() as tmp:
        rc, out, _ = run_cli(["init"])
        assert rc == 0
        assert (tmp / "team.json").exists()
        assert (tmp / "runtime_config.json").exists()
        assert "wrote" in out
        assert "Next:" in out


def test_init_default_team_has_three_workers_and_a_manager():
    with _tmp_env() as tmp:
        run_cli(["init"])
        team = json.loads((tmp / "team.json").read_text(encoding="utf-8"))
        assert team["session"] == "ClaudeTeam"
        assert team["default_model"] == "opus"
        assert set(team["agents"]) == {"manager", "worker_cc", "worker_codex", "worker_kimi"}
        assert team["agents"]["worker_codex"]["cli"] == "codex-cli"


def test_init_runtime_config_has_empty_chat_and_profile():
    with _tmp_env() as tmp:
        run_cli(["init"])
        rt = json.loads((tmp / "runtime_config.json").read_text(encoding="utf-8"))
        assert rt == {"chat_id": "", "lark_profile": ""}


def test_init_session_flag_overrides_default():
    with _tmp_env() as tmp:
        run_cli(["init", "--session", "Alpha"])
        team = json.loads((tmp / "team.json").read_text(encoding="utf-8"))
        assert team["session"] == "Alpha"


def test_init_refuses_to_overwrite_existing_team():
    with _tmp_env() as tmp:
        (tmp / "team.json").write_text('{"agents":{"x":{}}}', encoding="utf-8")
        rc, _, err = run_cli(["init"])
        assert rc == 1
        assert "team.json already exists" in err
        # the existing file is untouched
        assert "x" in (tmp / "team.json").read_text(encoding="utf-8")


def test_init_refuses_to_overwrite_existing_runtime_config():
    with _tmp_env() as tmp:
        (tmp / "runtime_config.json").write_text('{"chat_id":"oc_existing"}', encoding="utf-8")
        rc, _, err = run_cli(["init"])
        assert rc == 1
        assert "runtime_config.json already exists" in err


def test_init_force_overwrites_both():
    with _tmp_env() as tmp:
        (tmp / "team.json").write_text('{"agents":{"x":{}}}', encoding="utf-8")
        (tmp / "runtime_config.json").write_text('{"chat_id":"old"}', encoding="utf-8")
        rc, _, _ = run_cli(["init", "--force"])
        assert rc == 0
        team = json.loads((tmp / "team.json").read_text(encoding="utf-8"))
        assert "manager" in team["agents"]
        rt = json.loads((tmp / "runtime_config.json").read_text(encoding="utf-8"))
        assert rt["chat_id"] == ""


def test_init_help_returns_zero():
    rc, out, _ = run_cli(["init", "--help"])
    assert rc == 0
    assert "usage: claudeteam init" in out


def test_init_unknown_arg_returns_one():
    with _tmp_env():
        rc, _, err = run_cli(["init", "--bogus"])
        assert rc == 1
        assert "unexpected args" in err


def test_init_session_flag_combines_with_force():
    with _tmp_env() as tmp:
        (tmp / "team.json").write_text("{}", encoding="utf-8")
        (tmp / "runtime_config.json").write_text("{}", encoding="utf-8")
        rc, _, _ = run_cli(["init", "--force", "--session", "Beta"])
        assert rc == 0
        team = json.loads((tmp / "team.json").read_text(encoding="utf-8"))
        assert team["session"] == "Beta"


def test_init_writes_files_in_subdirs_when_envs_point_there():
    with isolated_env() as tmp:
        nested = tmp / "configs" / "team-alpha"
        with env_patch(
            CLAUDETEAM_TEAM_FILE=str(nested / "team.json"),
            CLAUDETEAM_RUNTIME_CONFIG=str(nested / "rc.json"),
        ):
            rc, _, _ = run_cli(["init"])
            assert rc == 0
            assert (nested / "team.json").exists()
            assert (nested / "rc.json").exists()
