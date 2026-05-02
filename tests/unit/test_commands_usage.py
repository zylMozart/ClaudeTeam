"""Tests for `claudeteam usage` — token-spend snapshot."""
from __future__ import annotations

import shutil
import subprocess

from helpers import attr_patch, isolated_env, run_cli
from claudeteam.commands import usage as _usage_mod


def _stub_runner(*, rc: int, output: str):
    """Replace subprocess.run only for ccusage invocations."""
    saved = subprocess.run

    class FakeResult:
        def __init__(self, returncode, stdout, stderr=""):
            self.returncode = returncode
            self.stdout = stdout
            self.stderr = stderr

    def fake(argv, *args, **kwargs):
        if argv[:1] == ["npx"]:
            return FakeResult(rc, output)
        return saved(argv, *args, **kwargs)

    return attr_patch(subprocess, run=fake)


def _stub_npx_present(present: bool):
    saved = shutil.which

    def fake(name, *args, **kwargs):
        if name == "npx":
            return "/usr/bin/npx" if present else None
        return saved(name, *args, **kwargs)

    return attr_patch(shutil, which=fake)


# ── happy path ──────────────────────────────────────────────────


def test_usage_runs_ccusage_for_claude_code_agents():
    team = {"agents": {"manager": {"cli": "claude-code"}}}
    with isolated_env(team=team), _stub_npx_present(True), \
            _stub_runner(rc=0, output="Day 1: 12345 tokens\nTotal: 12345"):
        rc, out, _ = run_cli(["usage"])
        assert rc == 0
        assert "claude-code (via ccusage)" in out
        assert "Day 1: 12345 tokens" in out


def test_usage_lists_other_clis_with_no_tool_message():
    team = {"agents": {"a": {"cli": "codex-cli"}, "b": {"cli": "kimi-code"}}}
    with isolated_env(team=team), _stub_npx_present(False):
        rc, out, _ = run_cli(["usage"])
        assert rc == 0
        assert "codex-cli: no upstream usage tool" in out
        assert "kimi-code: no upstream usage tool" in out


def test_usage_warns_on_ccusage_failure():
    team = {"agents": {"manager": {"cli": "claude-code"}}}
    with isolated_env(team=team), _stub_npx_present(True), \
            _stub_runner(rc=1, output="ccusage: not found"):
        rc, out, _ = run_cli(["usage"])
        assert rc == 0
        assert "ccusage failed" in out
        assert "ccusage: not found" in out


def test_usage_skips_ccusage_when_npx_missing():
    team = {"agents": {"manager": {"cli": "claude-code"}}}
    with isolated_env(team=team), _stub_npx_present(False):
        rc, out, _ = run_cli(["usage"])
        assert rc == 0
        assert "npx not on PATH" in out


# ── flags / parsing ─────────────────────────────────────────────


def test_usage_view_flag_threads_through():
    team = {"agents": {"manager": {"cli": "claude-code"}}}
    captured = {}

    def fake_run(view, *, runner=None):
        captured["view"] = view
        return 0, "ok"

    with attr_patch(_usage_mod, _run_ccusage=fake_run), \
            isolated_env(team=team), _stub_npx_present(True):
        rc, _, _ = run_cli(["usage", "--view", "monthly"])
        assert rc == 0
        assert captured["view"] == "monthly"


def test_usage_rejects_unknown_view():
    team = {"agents": {"manager": {"cli": "claude-code"}}}
    with isolated_env(team=team):
        rc, _, err = run_cli(["usage", "--view", "bogus"])
        assert rc == 1
        assert "unknown view" in err


def test_usage_rejects_unexpected_args():
    with isolated_env():
        rc, _, err = run_cli(["usage", "--bogus"])
        assert rc == 1
        assert "unexpected args" in err


def test_usage_help():
    rc, out, _ = run_cli(["usage", "--help"])
    assert rc == 0
    assert "usage: claudeteam usage" in out
