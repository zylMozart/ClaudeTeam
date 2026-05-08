"""Tests for agents/qwen_code.py — Alibaba Qwen Code CLI adapter shape."""
from __future__ import annotations

from claudeteam.agents.qwen_code import QwenCodeAdapter
from claudeteam.agents import known_clis, get_adapter


def test_spawn_cmd_uses_yolo_and_tags_agent():
    """Mirror claude-code / codex / gemini auto-approve pattern so the
    pane runs unattended; tag agent via QWEN_AGENT_NAME for log scrapes."""
    adapter = QwenCodeAdapter()
    cmd = adapter.spawn_cmd("worker_qwen", "qwen-72b")
    assert ("QWEN_AGENT_NAME='worker_qwen'" in cmd
            or "QWEN_AGENT_NAME=worker_qwen" in cmd)
    assert "--yolo" in cmd
    assert "DISABLE_UPDATE_CHECK=1" in cmd


def test_spawn_cmd_drops_model_arg():
    """qwen selects model via env / config, not argv. Adapter must NOT
    inject --model into spawn_cmd or qwen will reject the arg."""
    adapter = QwenCodeAdapter()
    cmd = adapter.spawn_cmd("worker_qwen", "qwen-72b")
    assert "--model" not in cmd
    assert "qwen-72b" not in cmd


def test_spawn_cmd_quotes_agent_name():
    """shlex.quote defends against agent names with spaces / quotes."""
    cmd = QwenCodeAdapter().spawn_cmd("with space", "")
    assert "'with space'" in cmd or "with\\ space" in cmd


def test_ready_markers_include_qwen_prompt():
    markers = QwenCodeAdapter().ready_markers()
    assert "qwen>" in markers
    assert any("Qwen" in m for m in markers)


def test_busy_markers_cover_thinking_and_spinner():
    busy = QwenCodeAdapter().busy_markers()
    assert "Thinking" in busy
    assert "⣾" in busy  # braille spinner from SPINNER_CHARS


def test_rate_limit_markers_cover_chinese_and_english():
    """qwen-code is bilingual — rate-limit messages may be EN or zh-CN.
    Both must trigger the rate-limit gate so deliver.apply skips inject."""
    markers = QwenCodeAdapter().rate_limit_markers()
    assert any("rate limit" in m for m in markers)
    assert any("请求过于频繁" in m for m in markers)
    assert "429" in markers


def test_process_name_is_qwen():
    """For /proc walkers and `pkill -f qwen` to work, process_name must
    match the binary's exec name."""
    assert QwenCodeAdapter().process_name() == "qwen"


def test_submit_keys_use_multiline_form():
    keys = QwenCodeAdapter().submit_keys()
    assert "M-Enter" in keys
    assert "Enter" in keys


def test_qwen_code_and_qwen_cli_alias_resolve_to_same_adapter():
    """Symmetry with kimi-code / kimi-cli alias pair: both names yield
    the SAME adapter instance so config drift between the two values
    can't cause different behaviour."""
    code_a = get_adapter("qwen-code")
    cli_a = get_adapter("qwen-cli")
    assert isinstance(code_a, QwenCodeAdapter)
    assert isinstance(cli_a, QwenCodeAdapter)
    assert code_a is cli_a


def test_registered_in_known_clis():
    names = known_clis()
    assert "qwen-code" in names
    assert "qwen-cli" in names
