"""Tests for the CLI adapter registry + each adapter's spawn / markers contract."""
from __future__ import annotations

from claudeteam.agents import get_adapter, known_clis
from claudeteam.agents.base import CliAdapter
from claudeteam.agents.claude_code import ClaudeCodeAdapter
from claudeteam.agents.codex_cli import CodexCliAdapter
from claudeteam.agents.kimi_code import KimiCodeAdapter
from claudeteam.agents.gemini_cli import GeminiCliAdapter
from claudeteam.agents.qwen_code import QwenCodeAdapter


# ── registry ──────────────────────────────────────────────────────


def test_registry_lists_known_clis_plus_kimi_and_qwen_aliases():
    """gemini-cli and qwen-code (+qwen-cli alias) are registered.
    kimi-cli + qwen-cli are aliases so both forms in team.json work."""
    names = set(known_clis())
    assert names == {
        "claude-code", "codex-cli", "gemini-cli",
        "kimi-code", "kimi-cli",
        "qwen-code", "qwen-cli",
        "minimax", "mini-agent",
        "opencode",
        "codewhale", "code-whale",
        "openclaw",
        "trae", "trae-cli",
        "hermes",
        "pi", "pi-cli",
    }


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
        assert adapter.process_name()
        assert adapter.submit_keys()


def test_default_submit_keys_are_enter_variants():
    # base default lists Enter / C-m / C-j; ClaudeCode keeps it.
    cc = ClaudeCodeAdapter().submit_keys()
    assert cc[0] == "Enter"
    # Codex submits on plain Enter (M-Enter is unreliable under tmux; C-j is a
    # newline, never a submit) → it leads with Enter and drops C-j.
    codex = CodexCliAdapter().submit_keys()
    assert codex[0] == "Enter"
    assert "C-j" not in codex
    # Kimi 1.47's TUI submits on plain Enter — M-Enter is NOT a submit in
    # this version (acceptance F-1: the old M-Enter primary left every
    # message unsubmitted in the composer).
    kimi = KimiCodeAdapter().submit_keys()
    assert kimi[0] == "Enter"
    assert "M-Enter" not in kimi


def test_interrupt_keys_are_uniform_escape_across_every_cli():
    """`/stop`'s interrupt must be CONSISTENT across all CLIs. Every
    registered adapter — claude-code / codex-cli / gemini-cli /
    kimi-code / qwen-code (+ aliases) — interrupts with Esc, not the
    old Ctrl-C."""
    for cli in known_clis():
        assert get_adapter(cli).interrupt_keys() == ["Escape"], cli


def test_resubmit_on_idle_true_except_kimi():
    """Autosubmit re-nudge is safe on claude/codex/gemini/qwen,
    but kimi's TUI reads the re-sent submit key as an interrupt → kimi alone
    opts out."""
    assert KimiCodeAdapter().resubmit_on_idle() is False
    for a in (ClaudeCodeAdapter(), CodexCliAdapter(),
              GeminiCliAdapter(), QwenCodeAdapter()):
        assert a.resubmit_on_idle() is True, type(a).__name__


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


def test_codex_spawn_sets_per_agent_codex_home():
    from claudeteam.agents.codex_cli import codex_home
    cmd = CodexCliAdapter().spawn_cmd("worker_codex", "")
    assert f"CODEX_HOME={codex_home('worker_codex')}" in cmd
    assert codex_home("worker_codex").endswith("/worker_codex/home/.codex")


def test_codex_native_memory_path_is_agents_md_under_codex_home():
    from claudeteam.agents.codex_cli import codex_home
    path = CodexCliAdapter().native_memory_path("worker_codex")
    assert path == f"{codex_home('worker_codex')}/AGENTS.md"


def test_codex_display_model_passes_openai_through_but_labels_dropped():
    a = CodexCliAdapter()
    assert a.display_model("gpt-5.5") == "gpt-5.5"
    assert a.display_model("o3") == "o3"
    # Dropped (non-OpenAI) → label the real source, not the stale alias.
    assert a.display_model("opus") == "codex 自身配置"
    assert a.display_model("") == "codex 自身配置"


def test_native_memory_reloads_only_claude_and_gemini():
    """The mid-session disk-reload capability gates the G reidentify
    fallback: claude (re-reads after /compact) + gemini (every-prompt +
    /memory reload) → True; codex/qwen/kimi load once at startup → False,
    so they need a reidentify re-inject to pick up a fresh anchor."""
    assert ClaudeCodeAdapter().native_memory_reloads() is True
    assert GeminiCliAdapter().native_memory_reloads() is True
    assert CodexCliAdapter().native_memory_reloads() is False
    assert QwenCodeAdapter().native_memory_reloads() is False
    assert KimiCodeAdapter().native_memory_reloads() is False


def test_kimi_has_no_native_memory_file_by_design():
    """E/Plan-B: kimi loads memory only via the git-root→cwd chain, so
    isolating a per-agent AGENTS.md would force the pane's cwd off the
    repo. We deliberately keep cwd=repo and skip the native file — kimi
    relies on the init-prompt anchor (+ reidentify fallback) instead.
    Pin it so a future change can't silently flip kimi to a colliding or
    cwd-moving native path without revisiting the rationale."""
    assert KimiCodeAdapter().native_memory_path("worker_kimi") is None


def test_kimi_spawn_uses_yolo_flag_and_disable_update():
    cmd = KimiCodeAdapter().spawn_cmd("worker_kimi", "")
    assert "kimi --yolo" in cmd
    assert "DISABLE_UPDATE_CHECK=1" in cmd
    assert "KIMI_AGENT=worker_kimi" in cmd


# ── kimi model bootstrap ─────────────


def test_kimi_passes_kimi_valid_team_model_via_dash_m():
    """A kimi/Moonshot model from team config is passed explicitly with -m
    (no longer dropped) so a respawned session can't land on 'LLM not set'."""
    cmd = KimiCodeAdapter().spawn_cmd("worker_kimi", "kimi-for-coding")
    assert "kimi --yolo -m kimi-for-coding" in cmd


def test_kimi_drops_non_kimi_model_but_force_applies_config_default():
    """A claude/gpt team alias isn't a kimi model → dropped; instead kimi's
    own config default_model is force-applied with -m (defeats kimi-cli's
    respawn quirk of not auto-loading it)."""
    import os
    import tempfile
    from pathlib import Path
    from claudeteam.agents import kimi_code
    from helpers import attr_patch
    with tempfile.TemporaryDirectory() as d:
        cfg = Path(d) / "config.toml"
        cfg.write_text('default_model = "kimi-code"\n')
        with attr_patch(kimi_code, _kimi_config_path=lambda: cfg):
            cmd = KimiCodeAdapter().spawn_cmd("worker_kimi", "claude-opus-4-8")
    assert "claude-opus-4-8" not in cmd          # claude alias dropped
    assert "kimi --yolo -m kimi-code" in cmd     # config default force-applied


def test_kimi_omits_dash_m_when_no_model_and_no_config():
    """No kimi-valid model + no readable config default → omit -m (graceful
    fallback to kimi's own auto path; never emit a broken `-m`)."""
    from pathlib import Path
    from claudeteam.agents import kimi_code
    from helpers import attr_patch
    with attr_patch(kimi_code,
                    _kimi_config_path=lambda: Path("/nonexistent/.kimi/config.toml")):
        cmd = KimiCodeAdapter().spawn_cmd("worker_kimi", "")
    assert " -m " not in cmd
    assert "kimi --yolo" in cmd


# ── markers ──────────────────────────────────────────────────────


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


