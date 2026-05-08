"""Tests for runtime/tunables.py — env > toml > default cascade."""
from __future__ import annotations

from pathlib import Path

from helpers import env_patch, isolated_env
from claudeteam.runtime import paths, tunables


def _write_toml(tmp_path: Path, content: str) -> None:
    """Write a claudeteam.toml in the isolated tmp dir + reset cache."""
    cfg = tmp_path / "claudeteam.toml"
    cfg.write_text(content, encoding="utf-8")
    tunables.reset_cache()


def _with_config_dir(tmp_path: Path):
    """Point CLAUDETEAM_CONFIG_FILE at our test toml so the resolver
    reads it instead of cwd."""
    return env_patch(CLAUDETEAM_CONFIG_FILE=str(tmp_path / "claudeteam.toml"))


# ── default fallback ──────────────────────────────────────────────


def test_default_when_no_config_and_no_env():
    with isolated_env() as tmp:
        with _with_config_dir(tmp):
            tunables.reset_cache()
            assert tunables.tunable("router.stale_event_threshold_s", 180.0) == 180.0


def test_default_when_toml_missing_intermediate_section():
    """toml exists but doesn't have [router] section → return default."""
    with isolated_env() as tmp:
        _write_toml(tmp, '[feishu]\nsend_as = "bot"\n')
        with _with_config_dir(tmp):
            tunables.reset_cache()
            assert tunables.tunable("router.stale_event_threshold_s", 180.0) == 180.0


# ── toml override ─────────────────────────────────────────────────


def test_toml_value_overrides_default():
    with isolated_env() as tmp:
        _write_toml(tmp, "[router]\nstale_event_threshold_s = 60\n")
        with _with_config_dir(tmp):
            tunables.reset_cache()
            assert tunables.tunable("router.stale_event_threshold_s", 180.0) == 60


def test_dotted_path_navigates_nested():
    with isolated_env() as tmp:
        _write_toml(tmp, '[chat.publish]\nuser_to_manager = "always"\n')
        with _with_config_dir(tmp):
            tunables.reset_cache()
            assert tunables.tunable("chat.publish.user_to_manager", False) == "always"


def test_list_field_keeps_toml_array():
    with isolated_env() as tmp:
        _write_toml(tmp, '[feishu]\nbroadcast_tokens = ["@team", "@all"]\n')
        with _with_config_dir(tmp):
            tunables.reset_cache()
            v = tunables.tunable("feishu.broadcast_tokens", [])
            assert v == ["@team", "@all"]


# ── env override ──────────────────────────────────────────────────


def test_env_var_overrides_toml():
    with isolated_env() as tmp:
        _write_toml(tmp, "[router]\nstale_event_threshold_s = 60\n")
        with _with_config_dir(tmp), \
                env_patch(CLAUDETEAM_ROUTER_STALE_EVENT_THRESHOLD_S="30"):
            tunables.reset_cache()
            assert tunables.tunable("router.stale_event_threshold_s", 180.0) == 30.0


def test_env_var_overrides_default_when_no_toml():
    with isolated_env() as tmp:
        with _with_config_dir(tmp), \
                env_patch(CLAUDETEAM_ROUTER_STALE_EVENT_THRESHOLD_S="45"):
            tunables.reset_cache()
            assert tunables.tunable("router.stale_event_threshold_s", 180.0) == 45.0


# ── type coercion ─────────────────────────────────────────────────


def test_bool_coerces_truthy_env():
    with isolated_env() as tmp:
        with _with_config_dir(tmp), \
                env_patch(CLAUDETEAM_FEISHU_NO_PROXY="true"):
            tunables.reset_cache()
            assert tunables.tunable("feishu.no_proxy", False) is True


def test_bool_coerces_falsy_env():
    with isolated_env() as tmp:
        with _with_config_dir(tmp), \
                env_patch(CLAUDETEAM_FEISHU_NO_PROXY="0"):
            tunables.reset_cache()
            assert tunables.tunable("feishu.no_proxy", True) is False


def test_int_coerces_env_string():
    with isolated_env() as tmp:
        with _with_config_dir(tmp), \
                env_patch(CLAUDETEAM_LIMITS_TMUX_CAPTURE_DEFAULT_LINES="25"):
            tunables.reset_cache()
            assert tunables.tunable("limits.tmux_capture_default_lines", 10) == 25


def test_garbage_env_falls_back_to_default():
    """env set to non-parseable → falls back to default (don't kill daemon)."""
    with isolated_env() as tmp:
        with _with_config_dir(tmp), \
                env_patch(CLAUDETEAM_ROUTER_STALE_EVENT_THRESHOLD_S="potato"):
            tunables.reset_cache()
            assert tunables.tunable("router.stale_event_threshold_s", 180.0) == 180.0


def test_empty_env_falls_through_to_toml():
    """Env set to empty string → ignored (treat as unset)."""
    with isolated_env() as tmp:
        _write_toml(tmp, "[router]\nstale_event_threshold_s = 60\n")
        with _with_config_dir(tmp), \
                env_patch(CLAUDETEAM_ROUTER_STALE_EVENT_THRESHOLD_S=""):
            tunables.reset_cache()
            assert tunables.tunable("router.stale_event_threshold_s", 180.0) == 60


def test_garbage_toml_returns_default_does_not_raise():
    with isolated_env() as tmp:
        _write_toml(tmp, "this is not = valid toml [\n")
        with _with_config_dir(tmp):
            tunables.reset_cache()
            assert tunables.tunable("router.stale_event_threshold_s", 180.0) == 180.0


def test_garbage_toml_warns_to_stderr():
    """On parse error, surface a one-line stderr warning so operator
    knows their toml changes aren't taking effect (vs silent fallback
    that hides the misconfig)."""
    import contextlib, io
    err = io.StringIO()
    with isolated_env() as tmp:
        _write_toml(tmp, "[chat.publish]\nfoo = false\n[chat.publish]\nbar = true\n")  # duplicate section
        with _with_config_dir(tmp), contextlib.redirect_stderr(err):
            tunables.reset_cache()
            tunables.tunable("router.stale_event_threshold_s", 180.0)
    assert "解析失败" in err.getvalue()


def test_garbage_toml_warning_dedups_per_mtime():
    """Repeated tunable() calls on the same broken toml shouldn't spam
    stderr — warn once per (path, mtime)."""
    import contextlib, io
    err = io.StringIO()
    with isolated_env() as tmp:
        _write_toml(tmp, "this is not [valid\n")
        with _with_config_dir(tmp), contextlib.redirect_stderr(err):
            tunables.reset_cache()
            tunables.tunable("a", "x")
            tunables.tunable("b", "y")
            tunables.tunable("c", "z")
    assert err.getvalue().count("解析失败") == 1
