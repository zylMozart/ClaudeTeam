"""Tests for runtime/lifecycle.py — pane_env_prefix + provision_pane.

Both helpers were extracted in round-16 from `commands/start.py` /
`commands/hire.py` but never got their own unit test (CLAUDE.md rule:
every new module ships its own unit test). The behaviour was covered
transitively through start/hire integration tests; this file pins
provision_pane directly for each of its four outcomes (LAZY / READY /
READY_NO_INIT / SPAWN_FAILED).
"""
from __future__ import annotations

from pathlib import Path

from helpers import attr_patch, env_patch, isolated_env, tmux_patch
from claudeteam.runtime import lifecycle, tmux, wake
from claudeteam.runtime.lifecycle import (
    LAZY, READY, READY_NO_INIT, SPAWN_FAILED, CONFIG_ERROR,
    pane_env_prefix, provision_pane,
)
from claudeteam.store import local_facts


# ── pane_env_prefix ───────────────────────────────────────────────


def test_pane_env_prefix_always_includes_state_dir():
    """Even with no other env set, STATE_DIR is always emitted so the
    spawned pane never falls back to ~/.claudeteam."""
    with isolated_env(team={"agents": {"a": {}}}):
        prefix = pane_env_prefix("a")
    assert prefix.startswith("CLAUDETEAM_STATE_DIR=")


def test_pane_env_prefix_propagates_lark_profile_when_set():
    with isolated_env(team={"agents": {"a": {}}}), env_patch(
            LARK_CLI_PROFILE="prod"):
        prefix = pane_env_prefix("a")
    assert "LARK_CLI_PROFILE=prod" in prefix


def test_pane_env_prefix_skips_unset_vars():
    """Vars not present in the operator shell don't pollute the prefix."""
    with isolated_env(team={"agents": {"a": {}}}), env_patch(
            LARK_CLI_PROFILE=None,
            LARK_CLI_NO_PROXY=None,
            CLAUDETEAM_LARK_SEND_AS=None,
            CLAUDETEAM_DEFAULT_MODEL=None):
        prefix = pane_env_prefix("a")
    # Only state_dir survives (team_file/runtime_config are set by isolated_env)
    assert "LARK_CLI_PROFILE=" not in prefix
    assert "LARK_CLI_NO_PROXY=" not in prefix


def test_pane_env_prefix_propagates_feishu_app_credentials():
    """Bringup B5: tmux server started by an earlier checkout had its
    own global env without FEISHU_APP_*; new panes inherited that env
    and tenant_token_from_env() returned None → fell back to the saved
    lark-cli profile (an OLD app) → HTTP 400 on every claudeteam say.
    Embedding the creds in the spawn-cmd prefix sidesteps the
    tmux-server-env quirk."""
    with isolated_env(team={"agents": {"a": {}}}), env_patch(
            FEISHU_APP_ID="cli_NEW",
            FEISHU_APP_SECRET="newSecret123",
            LARKSUITE_CLI_APP_ID="cli_NEW",
            LARKSUITE_CLI_APP_SECRET="newSecret123"):
        prefix = pane_env_prefix("a")
    assert "FEISHU_APP_ID=cli_NEW" in prefix
    assert "FEISHU_APP_SECRET=newSecret123" in prefix
    assert "LARKSUITE_CLI_APP_ID=cli_NEW" in prefix
    assert "LARKSUITE_CLI_APP_SECRET=newSecret123" in prefix


def test_pane_env_prefix_shell_quotes_paths_with_spaces():
    """shlex.quote should wrap any value containing whitespace; otherwise
    `eval $(...)` in a downstream shell would split on the space."""
    with isolated_env(team={"agents": {"a": {}}}), env_patch(
            LARK_CLI_PROFILE="my profile"):
        prefix = pane_env_prefix("a")
    # quoted form: 'my profile' (single quotes) — never raw `my profile`
    assert "'my profile'" in prefix


# ── provision_pane: LAZY ──────────────────────────────────────────


def test_provision_lazy_agent_sets_待命_and_skips_spawn():
    """Lazy agents in team.json get status 待命; spawn_agent is never
    called (the pane stays at a shell prompt)."""
    team = {"agents": {"sleepy": {"cli": "claude-code", "lazy": True}}}
    spawn_calls = []
    with isolated_env(team=team), tmux_patch(
            spawn_agent=lambda t, c: spawn_calls.append((str(t), c)) or True):
        outcome = provision_pane("sleepy", tmux.Target("S", "sleepy"))
        assert outcome == LAZY
        assert spawn_calls == []
        snap = local_facts.get_status("sleepy")
        assert snap["status"] == "待命"
        assert "lazy" in snap["task"]


# ── provision_pane: SPAWN_FAILED ──────────────────────────────────


def test_provision_spawn_failure_returns_spawn_failed():
    team = {"agents": {"a": {"cli": "claude-code"}}}
    with isolated_env(team=team), tmux_patch(
            spawn_agent=lambda t, c: False):
        outcome = provision_pane("a", tmux.Target("S", "a"))
    assert outcome == SPAWN_FAILED


# ── provision_pane: READY (happy path) ────────────────────────────


def test_provision_ready_spawns_then_injects_init_prompt():
    """Happy path: spawn succeeds, wait_until_ready true, identity init
    is injected, status flips to 进行中."""
    team = {"agents": {"alice": {"cli": "claude-code", "model": "opus"}}}
    spawn_calls = []
    inject_calls = []
    with isolated_env(team=team), tmux_patch(
            spawn_agent=lambda t, c: spawn_calls.append((str(t), c)) or True,
            inject=lambda t, text, **kw: inject_calls.append((str(t), text)) or True), \
            attr_patch(wake, wait_until_ready=lambda *a, **kw: True):
        outcome = provision_pane("alice", tmux.Target("S", "alice"))
        assert outcome == READY
        assert len(spawn_calls) == 1
        # Identity init prompt was injected after spawn
        assert len(inject_calls) == 1
        assert "alice" in inject_calls[0][1]
        assert "identity.md" in inject_calls[0][1]
        snap = local_facts.get_status("alice")
        assert snap["status"] == "进行中"


def test_provision_ready_pane_env_prefix_baked_into_spawn_cmd():
    team = {"agents": {"a": {"cli": "claude-code"}}}
    spawn_calls = []
    with isolated_env(team=team), tmux_patch(
            spawn_agent=lambda t, c: spawn_calls.append((str(t), c)) or True,
            inject=lambda *a, **kw: True), \
            attr_patch(wake, wait_until_ready=lambda *a, **kw: True):
        provision_pane("a", tmux.Target("S", "a"))
    cmd = spawn_calls[0][1]
    assert "CLAUDETEAM_STATE_DIR=" in cmd
    # Adapter contributed the actual CLI spawn after the env prefix
    assert "claude" in cmd


# ── provision_pane: READY_NO_INIT ─────────────────────────────────


def test_provision_ready_no_init_when_marker_never_appears():
    """When wait_until_ready times out, spawn already happened so the
    pane is alive — status still flips to 进行中, but the identity
    init prompt is NOT injected (no point injecting into a CLI that
    might still be loading)."""
    team = {"agents": {"a": {"cli": "claude-code"}}}
    inject_calls = []
    with isolated_env(team=team), tmux_patch(
            spawn_agent=lambda t, c: True,
            inject=lambda t, text, **kw: inject_calls.append((str(t), text)) or True), \
            attr_patch(wake, wait_until_ready=lambda *a, **kw: False):
        outcome = provision_pane("a", tmux.Target("S", "a"))
        assert outcome == READY_NO_INIT
        assert inject_calls == []  # no identity init when CLI not ready
        snap = local_facts.get_status("a")
        assert snap["status"] == "进行中"  # status still flips


# ── provision_pane: CONFIG_ERROR (round-61) ──────────────────────


def test_provision_returns_config_error_on_unknown_cli():
    """REGRESSION: a typo in team.json's `cli` field (e.g. 'claude-cod'
    missing the e) used to raise KeyError straight through start.py,
    killing the entire claudeteam start. Now returns CONFIG_ERROR so
    the caller can warn + skip + continue with the rest of the team."""
    import io
    import contextlib
    team = {"agents": {"typo_agent": {"cli": "claude-cod"}}}  # unknown CLI
    err = io.StringIO()
    with isolated_env(team=team), \
            contextlib.redirect_stderr(err):
        outcome = provision_pane("typo_agent", tmux.Target("S", "typo_agent"))
    assert outcome == CONFIG_ERROR
    # Stderr explains which agent + what's wrong
    assert "typo_agent" in err.getvalue()
    assert "claude-cod" in err.getvalue() or "unknown cli" in err.getvalue()


# ── _ensure_claude_agent_home (R172.b) ───────────────────────────


def test_ensure_claude_agent_home_does_not_raise_when_data_missing():
    """On hosts without /data (macOS, test runners), the helper falls
    back to <state_dir>/agent-home/<agent>. Boss-flagged 2026-05-05:
    don't crash claudeteam start outside Docker."""
    import os
    if os.path.exists("/data"):
        return  # skip on Linux containers; helper does real work there
    # Must not raise on missing /data — falls back to state_dir
    lifecycle._ensure_claude_agent_home("manager")
    lifecycle._ensure_claude_agent_home("worker_cc")


def test_ensure_claude_agent_home_writes_keychain_extract_as_regular_file():
    """macOS host: when `security find-generic-password` succeeds, write
    the result as a *regular file* (not a symlink). Earlier impl
    symlinked to ~/.claude/.credentials.json which (a) goes stale
    versus the live keychain and (b) gets atomic-replaced by claude on
    refresh, defeating the share intent. 2026-05-07 host smoke ate
    'refreshToken: ""' for breakfast — pin the regular-file invariant."""
    import os
    import platform
    if platform.system() != "Darwin":
        return  # macOS-only path
    import subprocess
    fresh_creds = ('{"claudeAiOauth":{"accessToken":"a-tok",'
                   '"refreshToken":"r-tok","expiresAt":9999999999000}}')
    def fake_run(argv, **kw):
        return subprocess.CompletedProcess(
            args=argv, returncode=0, stdout=fresh_creds, stderr="")
    with isolated_env(team={"agents": {"manager": {"cli": "claude-code"}}}), \
            attr_patch(subprocess, run=fake_run):
        lifecycle._ensure_claude_agent_home("manager")
        from claudeteam.agents.claude_code import agent_home
        cred = Path(agent_home("manager")) / ".claude" / ".credentials.json"
        assert cred.exists(), "creds file not materialised"
        assert not cred.is_symlink(), "expected regular file, got symlink"
        assert "r-tok" in cred.read_text(), \
            "expected fresh keychain content, got stale"


def test_ensure_claude_agent_home_overwrites_stale_creds_each_call():
    """Re-extract on every call: prior stale snapshot is overwritten so
    `claudeteam down && claudeteam up` actually re-materialises from
    keychain. Old impl gated on `if not cred_link.exists()` so the
    file never refreshed once written."""
    import os
    import platform
    if platform.system() != "Darwin":
        return
    import subprocess
    tokens = iter(["v1-tok", "v2-tok"])
    def fake_run(argv, **kw):
        tok = next(tokens, "vN-tok")
        body = ('{"claudeAiOauth":{"accessToken":"a","refreshToken":"%s",'
                '"expiresAt":9999999999000}}' % tok)
        return subprocess.CompletedProcess(
            args=argv, returncode=0, stdout=body, stderr="")
    with isolated_env(team={"agents": {"manager": {"cli": "claude-code"}}}), \
            attr_patch(subprocess, run=fake_run):
        lifecycle._ensure_claude_agent_home("manager")
        from claudeteam.agents.claude_code import agent_home
        cred = Path(agent_home("manager")) / ".claude" / ".credentials.json"
        assert "v1-tok" in cred.read_text()
        lifecycle._ensure_claude_agent_home("manager")
        # Second call must replace the file with v2's content
        assert "v2-tok" in cred.read_text(), \
            "stale snapshot not overwritten on re-provision"
