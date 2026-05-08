"""Tests for runtime/config.py — team.json + runtime_config.json loading."""
from __future__ import annotations

from helpers import env_patch, isolated_env

from claudeteam.agents import adapter_for_agent
from claudeteam.agents.codex_cli import CodexCliAdapter
from claudeteam.agents.kimi_code import KimiCodeAdapter
from claudeteam.runtime import config


def _team_env(team_data, runtime_data=None):
    """Sugar over isolated_env(team=..., runtime_config=...) — keeps the
    older positional API the tests in this file have always used."""
    return isolated_env(team=team_data, runtime_config=runtime_data)


# ── team.json basics ────────────────────────────────────────────


def test_load_team_returns_full_dict():
    team = {"session": "test", "agents": {"a": {"cli": "claude-code"}}, "default_model": "opus"}
    with _team_env(team):
        loaded = config.load_team()
        assert loaded["session"] == "test"
        assert "a" in loaded["agents"]


def test_load_team_returns_default_when_missing():
    # isolated_env points CLAUDETEAM_TEAM_FILE at a tempdir that has no
    # team.json (since team= isn't passed), so config.load_team() takes
    # the missing-file → default-dict path.
    with isolated_env():
        t = config.load_team()
        assert t["agents"] == {}
        assert "session" in t


def test_session_name_falls_back_to_claudeteam():
    with _team_env({"agents": {}}):
        assert config.session_name() == "ClaudeTeam"


def test_agent_names_sorted():
    team = {"agents": {"z": {}, "a": {}, "m": {}}}
    with _team_env(team):
        assert config.agent_names() == ["a", "m", "z"]


# ── per-agent config ────────────────────────────────────────────


def test_agent_config_returns_copy():
    team = {"agents": {"a": {"cli": "claude-code", "model": "opus"}}}
    with _team_env(team):
        cfg = config.agent_config("a")
        cfg["model"] = "modified"
        # original team.json untouched
        assert config.agent_config("a")["model"] == "opus"


def test_agent_config_unknown_raises_keyerror():
    with _team_env({"agents": {}}):
        try:
            config.agent_config("ghost")
        except KeyError as exc:
            assert "ghost" in str(exc)
        else:
            raise AssertionError("expected KeyError")


def test_agent_cli_defaults_to_claude_code():
    team = {"agents": {"a": {}}}
    with _team_env(team):
        assert config.agent_cli("a") == "claude-code"


def test_agent_cli_respects_explicit_value():
    team = {"agents": {"a": {"cli": "codex-cli"}}}
    with _team_env(team):
        assert config.agent_cli("a") == "codex-cli"


# ── model resolution chain ──────────────────────────────────────


def test_agent_model_uses_agent_specific_first():
    team = {"agents": {"a": {"model": "haiku"}}, "default_model": "opus"}
    with _team_env(team):
        assert config.agent_model("a") == "haiku"


def test_agent_model_uses_env_default_when_no_agent_model():
    team = {"agents": {"a": {}}, "default_model": "opus"}
    with _team_env(team), env_patch(CLAUDETEAM_DEFAULT_MODEL="sonnet"):
        assert config.agent_model("a") == "sonnet"


def test_agent_model_uses_team_default_when_no_env():
    team = {"agents": {"a": {}}, "default_model": "opus"}
    with _team_env(team), env_patch(CLAUDETEAM_DEFAULT_MODEL=None):
        assert config.agent_model("a") == "opus"


def test_agent_model_falls_back_to_opus_constant():
    team = {"agents": {"a": {}}}  # no default_model
    with _team_env(team), env_patch(CLAUDETEAM_DEFAULT_MODEL=None):
        assert config.agent_model("a") == "opus"


# ── runtime_config.json ─────────────────────────────────────────


def test_load_runtime_config_returns_empty_dict_when_missing():
    with _team_env({"agents": {}}):  # no runtime_data → file doesn't exist
        assert config.load_runtime_config() == {}


def test_chat_id_reads_runtime_config():
    with _team_env({"agents": {}}, runtime_data={"chat_id": "oc_xxx"}):
        assert config.chat_id() == "oc_xxx"


def test_chat_id_empty_when_unset():
    with _team_env({"agents": {}}, runtime_data={}):
        assert config.chat_id() == ""


def test_lark_profile_env_beats_file():
    with _team_env({"agents": {}}, runtime_data={"lark_profile": "from-file"}), \
            env_patch(LARK_CLI_PROFILE="from-env"):
        assert config.lark_profile() == "from-env"


def test_lark_profile_falls_back_to_file_when_env_unset():
    with _team_env({"agents": {}}, runtime_data={"lark_profile": "from-file"}), \
            env_patch(LARK_CLI_PROFILE=None):
        assert config.lark_profile() == "from-file"


# ── claudeteam.toml unified config (preferred over legacy json) ──


def _write_toml(tmp_dir, content: str):
    """Drop a claudeteam.toml in tmp + reset tunables cache."""
    from claudeteam.runtime import tunables
    (tmp_dir / "claudeteam.toml").write_text(content, encoding="utf-8")
    tunables.reset_cache()


def test_load_team_prefers_toml_over_legacy_json():
    """Both files exist → toml wins. Lets ops migrate without deleting
    old json; old json sticks around as a backup."""
    legacy = {"session": "from-legacy", "agents": {"old": {"cli": "claude-code"}}}
    with _team_env(legacy) as tmp:
        _write_toml(tmp, """
[team]
session = "from-toml"
default_model = "opus"

[team.agents.new]
cli = "claude-code"
role = "新员工"
""")
        with env_patch(CLAUDETEAM_CONFIG_FILE=str(tmp / "claudeteam.toml")):
            loaded = config.load_team()
        assert loaded["session"] == "from-toml"
        assert "new" in loaded["agents"]
        assert "old" not in loaded["agents"]


def test_load_team_falls_back_to_json_when_toml_missing():
    legacy = {"session": "S", "agents": {"a": {"cli": "claude-code"}}}
    with _team_env(legacy) as tmp:
        # No toml written. CONFIG_FILE points at non-existent path.
        with env_patch(CLAUDETEAM_CONFIG_FILE=str(tmp / "missing.toml")):
            loaded = config.load_team()
        assert loaded["session"] == "S"
        assert "a" in loaded["agents"]


def test_chat_id_prefers_toml():
    with _team_env({"agents": {}}, runtime_data={"chat_id": "oc_legacy"}) as tmp:
        _write_toml(tmp, 'chat_id = "oc_from_toml"\n')
        with env_patch(CLAUDETEAM_CONFIG_FILE=str(tmp / "claudeteam.toml")):
            assert config.chat_id() == "oc_from_toml"


def test_chat_id_falls_back_to_legacy_runtime_config():
    with _team_env({"agents": {}}, runtime_data={"chat_id": "oc_legacy"}) as tmp:
        with env_patch(CLAUDETEAM_CONFIG_FILE=str(tmp / "missing.toml")):
            assert config.chat_id() == "oc_legacy"


def test_lark_profile_priority_env_then_toml_then_legacy():
    """Three-way priority. env beats both; toml beats legacy json."""
    with _team_env({"agents": {}}, runtime_data={"lark_profile": "legacy"}) as tmp:
        _write_toml(tmp, 'lark_profile = "from-toml"\n')
        with env_patch(CLAUDETEAM_CONFIG_FILE=str(tmp / "claudeteam.toml"),
                       LARK_CLI_PROFILE="from-env"):
            assert config.lark_profile() == "from-env"
        with env_patch(CLAUDETEAM_CONFIG_FILE=str(tmp / "claudeteam.toml"),
                       LARK_CLI_PROFILE=None):
            assert config.lark_profile() == "from-toml"
        with env_patch(CLAUDETEAM_CONFIG_FILE=str(tmp / "missing.toml"),
                       LARK_CLI_PROFILE=None):
            assert config.lark_profile() == "legacy"


def test_save_runtime_config_roundtrip():
    with _team_env({"agents": {}}):
        config.save_runtime_config({"chat_id": "oc_new", "lark_profile": "p"})
        loaded = config.load_runtime_config()
        assert loaded == {"chat_id": "oc_new", "lark_profile": "p"}


# ── adapter_for_agent integration ───────────────────────────────


def test_adapter_for_agent_uses_team_json_cli_field():
    team = {"agents": {"w_codex": {"cli": "codex-cli"}, "w_kimi": {"cli": "kimi-code"}}}
    with _team_env(team):
        assert isinstance(adapter_for_agent("w_codex"), CodexCliAdapter)
        assert isinstance(adapter_for_agent("w_kimi"), KimiCodeAdapter)


def test_adapter_for_agent_unknown_agent_raises():
    with _team_env({"agents": {}}):
        try:
            adapter_for_agent("ghost")
        except KeyError:
            pass
        else:
            raise AssertionError("expected KeyError")


# ── lenient JSONDecodeError handling ─────────────────────────────


def test_load_team_returns_default_on_corrupt_json_with_warning():
    """REGRESSION: a malformed team.json used to raise JSONDecodeError
    straight through every claudeteam command. Now it falls back to
    the default + emits a stderr warning so the operator can fix the
    file without losing access to the CLI."""
    import io
    import contextlib
    with isolated_env() as tmp:
        team_path = tmp / "team.json"
        team_path.write_text("{ this is not valid json", encoding="utf-8")
        err = io.StringIO()
        with contextlib.redirect_stderr(err):
            loaded = config.load_team()
        # Default — empty agents dict
        assert loaded.get("agents") == {}
        assert loaded.get("session") == "ClaudeTeam"
        assert "team.json" in err.getvalue()
        assert "not valid JSON" in err.getvalue()


def test_load_runtime_config_returns_default_on_corrupt_json():
    """Sister case for runtime_config.json — same fallback semantics."""
    import io
    import contextlib
    with isolated_env() as tmp:
        rt_path = tmp / "runtime_config.json"
        rt_path.write_text("not-json-at-all", encoding="utf-8")
        err = io.StringIO()
        with contextlib.redirect_stderr(err):
            loaded = config.load_runtime_config()
        assert loaded == {}
        assert "runtime_config.json" in err.getvalue()
        assert "not valid JSON" in err.getvalue()


def test_session_name_falls_back_to_default_when_team_corrupt():
    """Downstream accessor `session_name()` should also degrade
    gracefully — `claudeteam start` shouldn't blow up just because
    team.json got truncated."""
    with isolated_env() as tmp:
        (tmp / "team.json").write_text("{partial", encoding="utf-8")
        import io, contextlib
        with contextlib.redirect_stderr(io.StringIO()):
            assert config.session_name() == "ClaudeTeam"
            assert config.agent_names() == []


def test_load_team_falls_back_on_oserror_with_warning():
    """If the file is present but unreadable (e.g. permission denied,
    encoding error), the lenient loader should still return the default
    + warn. Easier to stage via attr_patch on read_json since we can't
    portably create an unreadable file in CI."""
    import io
    import contextlib
    from helpers import attr_patch
    from claudeteam.runtime import config as cfg_module

    def boom(*a, **kw):
        raise OSError("[Errno 13] Permission denied")

    with isolated_env(team={"agents": {"a": {}}}):
        # The file IS valid; we're simulating an OS-level read failure
        err = io.StringIO()
        with attr_patch(cfg_module, read_json=boom), \
                contextlib.redirect_stderr(err):
            loaded = config.load_team()
        # default: empty agents, default session
        assert loaded.get("agents") == {}
        assert "team.json" in err.getvalue()
        assert "unreadable" in err.getvalue()
