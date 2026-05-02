"""Tests for the CLI adapter registry + each adapter's spawn / markers contract."""
from __future__ import annotations

from claudeteam.agents import get_adapter, known_clis
from claudeteam.agents.base import CliAdapter
from claudeteam.agents.claude_code import ClaudeCodeAdapter
from claudeteam.agents.codex_cli import CodexCliAdapter
from claudeteam.agents.kimi_code import KimiCodeAdapter


# ── registry ──────────────────────────────────────────────────────


def test_registry_lists_three_known_clis_plus_kimi_alias():
    names = set(known_clis())
    assert names == {"claude-code", "codex-cli", "kimi-code", "kimi-cli"}


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
