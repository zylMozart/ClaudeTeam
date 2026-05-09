"""Tests for the CLI adapter registry + each adapter's spawn / markers contract."""
from __future__ import annotations

from helpers import env_patch, isolated_env
from claudeteam.agents import get_adapter, known_clis
from claudeteam.agents.base import CliAdapter
from claudeteam.agents.claude_code import ClaudeCodeAdapter, agent_home
from claudeteam.agents.codex_cli import CodexCliAdapter
from claudeteam.agents.kimi_code import KimiCodeAdapter


# ── registry ──────────────────────────────────────────────────────


def test_registry_lists_known_clis_plus_kimi_and_qwen_aliases():
    """Round-85 added gemini-cli; round-101 added qwen-code (+qwen-cli
    alias). kimi-cli + qwen-cli are aliases so both forms in team.json
    work."""
    names = set(known_clis())
    assert names == {
        "claude-code", "codex-cli", "gemini-cli",
        "kimi-code", "kimi-cli",
        "qwen-code", "qwen-cli",
    }


def test_get_adapter_returns_matching_concrete_type():
    assert isinstance(get_adapter("claude-code"), ClaudeCodeAdapter)
    assert isinstance(get_adapter("codex-cli"), CodexCliAdapter)
    assert isinstance(get_adapter("kimi-code"), KimiCodeAdapter)


def test_kimi_alias_returns_same_instance():
    assert get_adapter("kimi-code") is get_adapter("kimi-cli")


def test_get_adapter_unknown_raises_keyerror_with_known_list():
    try:
        get_adapter("not-a-cli")
    except KeyError as exc:
        msg = str(exc)
        assert "unknown cli" in msg
        for name in ("claude-code", "codex-cli", "kimi-code"):
            assert name in msg
    else:
        raise AssertionError("expected KeyError for unknown cli")


# ── base + interface compliance ──────────────────────────────────


def _all_adapters() -> list[CliAdapter]:
    return [ClaudeCodeAdapter(), CodexCliAdapter(), KimiCodeAdapter()]


def test_every_adapter_implements_required_methods():
    for adapter in _all_adapters():
        assert isinstance(adapter, CliAdapter)
        cmd = adapter.spawn_cmd("worker_x", "sonnet")
        assert isinstance(cmd, str) and cmd.strip()
        ready = adapter.ready_markers()
        assert ready and isinstance(ready, list)
        busy = adapter.busy_markers()
        assert busy and isinstance(busy, list)
        assert adapter.process_name()
        assert adapter.submit_keys()


def test_default_submit_keys_are_enter_variants():
    # base default lists Enter / C-m / C-j; ClaudeCode keeps it, Codex/Kimi prepend M-Enter
    cc = ClaudeCodeAdapter().submit_keys()
    assert cc[0] == "Enter"
    for adapter in (CodexCliAdapter(), KimiCodeAdapter()):
        keys = adapter.submit_keys()
        assert keys[0] == "M-Enter"
        assert "Enter" in keys


# ── per-adapter spawn shape ──────────────────────────────────────


def test_claude_code_spawn_is_dangerously_skip_permissions_with_model():
    cmd = ClaudeCodeAdapter().spawn_cmd("worker_cc", "sonnet-4-6")
    assert "claude --dangerously-skip-permissions" in cmd
    assert "--model sonnet-4-6" in cmd
    assert "--name worker_cc" in cmd
    assert "IS_SANDBOX=1" in cmd


def test_codex_spawn_passes_openai_model_through():
    cmd = CodexCliAdapter().spawn_cmd("worker_codex", "gpt-5.5")
    assert "codex" in cmd
    assert "--dangerously-bypass-approvals-and-sandbox" in cmd
    assert "--model gpt-5.5" in cmd
    assert "CODEX_AGENT=worker_codex" in cmd


def test_codex_spawn_drops_non_openai_model():
    cmd = CodexCliAdapter().spawn_cmd("worker_codex", "sonnet")
    assert "--model" not in cmd  # silently dropped
    assert "--dangerously-bypass-approvals-and-sandbox" in cmd


def test_codex_spawn_quotes_agent_name_with_special_chars():
    cmd = CodexCliAdapter().spawn_cmd("worker x", "")
    assert "'worker x'" in cmd  # shlex.quote


def test_kimi_spawn_uses_yolo_flag_and_disable_update():
    cmd = KimiCodeAdapter().spawn_cmd("worker_kimi", "")
    assert "kimi --yolo" in cmd
    assert "DISABLE_UPDATE_CHECK=1" in cmd
    assert "KIMI_AGENT=worker_kimi" in cmd


# ── agent_home env override (multi-team isolation) ───────────────


def test_agent_home_env_override_wins_over_data_default():
    """`CLAUDETEAM_AGENT_HOME_BASE` lets team B in a same-container
    multi-team deploy point its claude HOMEs at a distinct base —
    otherwise both teams' "manager" agents would share
    /data/agent-home/manager and clobber each other's
    ~/.claude.json + credential snapshot."""
    with env_patch(CLAUDETEAM_AGENT_HOME_BASE="/data/agent-home-b"):
        home = agent_home("manager")
    assert home == "/data/agent-home-b/manager"


def test_agent_home_env_override_strips_trailing_slash():
    """Operators set the base with or without a trailing slash; the
    join must produce a single separator either way (no
    `/data/agent-home-b//manager`)."""
    with env_patch(CLAUDETEAM_AGENT_HOME_BASE="/data/agent-home-b/"):
        home = agent_home("manager")
    assert home == "/data/agent-home-b/manager"


def test_agent_home_default_unchanged_when_override_unset():
    """Back-compat: with no env override + no /data probe interference,
    fall back to state_dir-relative (the host path). Single-team
    deploys keep their existing layout."""
    with isolated_env(), env_patch(CLAUDETEAM_AGENT_HOME_BASE=None):
        # Force the host fallback by patching the cached probe to False.
        from claudeteam.agents import claude_code as cc
        prev = cc._DATA_WRITABLE
        cc._DATA_WRITABLE = False
        try:
            home = agent_home("worker_x")
        finally:
            cc._DATA_WRITABLE = prev
    assert home.endswith("/agent-home/worker_x")


def test_agent_home_container_default_routes_to_data_when_writable():
    """Container default path: `/data/agent-home/` writable + no env
    override → `/data/agent-home/<agent>`. Closes the regression net
    around the canonical single-team deploy (the host-fallback case
    is covered by the test above; without this one, a future change
    that promoted the env override even when unset would silently
    redirect every existing single-team deploy)."""
    with env_patch(CLAUDETEAM_AGENT_HOME_BASE=None):
        from claudeteam.agents import claude_code as cc
        prev = cc._DATA_WRITABLE
        cc._DATA_WRITABLE = True
        try:
            home = agent_home("manager")
        finally:
            cc._DATA_WRITABLE = prev
    assert home == "/data/agent-home/manager"


# ── markers ──────────────────────────────────────────────────────


def test_codex_busy_markers_include_boot_phase():
    """R-busy fix carries over: Booting MCP server must be a busy marker so
    inject_when_idle waits past the boot race."""
    assert "Booting MCP server" in CodexCliAdapter().busy_markers()


def test_kimi_busy_markers_include_using_shell():
    assert "Using Shell" in KimiCodeAdapter().busy_markers()
    assert "Booting" in KimiCodeAdapter().busy_markers()


def test_process_names_match_expected_binaries():
    assert ClaudeCodeAdapter().process_name() == "claude"
    assert CodexCliAdapter().process_name() == "codex"
    assert KimiCodeAdapter().process_name() == "kimi"


# ── codex_cli.ensure_workdir_trusted ─────────────────────────────


def test_ensure_workdir_trusted_writes_entry_when_config_missing(tmp_path=None):
    import tempfile
    from pathlib import Path
    from claudeteam.agents.codex_cli import ensure_workdir_trusted

    with tempfile.TemporaryDirectory() as tmp:
        cfg = Path(tmp) / "codex" / "config.toml"
        workdir = Path("/some/work/dir")
        ensure_workdir_trusted(workdir, config_path=cfg)
        text = cfg.read_text(encoding="utf-8")
        assert '[projects."/some/work/dir"]' in text
        assert 'trust_level = "trusted"' in text


def test_ensure_workdir_trusted_appends_when_other_entries_present():
    import tempfile
    from pathlib import Path
    from claudeteam.agents.codex_cli import ensure_workdir_trusted

    with tempfile.TemporaryDirectory() as tmp:
        cfg = Path(tmp) / "config.toml"
        cfg.write_text('[projects."/other/dir"]\ntrust_level = "trusted"\n', encoding="utf-8")
        ensure_workdir_trusted(Path("/new/dir"), config_path=cfg)
        text = cfg.read_text(encoding="utf-8")
        assert '[projects."/other/dir"]' in text
        assert '[projects."/new/dir"]' in text


def test_ensure_workdir_trusted_idempotent_when_entry_exists():
    import tempfile
    from pathlib import Path
    from claudeteam.agents.codex_cli import ensure_workdir_trusted

    with tempfile.TemporaryDirectory() as tmp:
        cfg = Path(tmp) / "config.toml"
        original = '[projects."/already/here"]\ntrust_level = "trusted"\n'
        cfg.write_text(original, encoding="utf-8")
        ensure_workdir_trusted(Path("/already/here"), config_path=cfg)
        # File unchanged
        assert cfg.read_text(encoding="utf-8") == original
