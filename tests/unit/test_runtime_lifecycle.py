"""Tests for runtime/lifecycle.py — pane_env_prefix + provision_pane.

Both helpers were extracted from `commands/start.py` /
`commands/hire.py` but never got their own unit test (CLAUDE.md rule:
every new module ships its own unit test). The behaviour was covered
transitively through start/hire integration tests; this file pins
provision_pane directly for each of its four outcomes (LAZY / READY /
READY_NO_INIT / SPAWN_FAILED).
"""
from __future__ import annotations

from pathlib import Path

from helpers import attr_patch, env_patch, isolated_env, tmux_patch
from claudeteam.runtime import lifecycle, paths, tmux, wake
from claudeteam.runtime.lifecycle import (
    LAZY, READY, READY_NO_INIT, SPAWN_FAILED, CONFIG_ERROR,
    build_spawn_command, pane_env_prefix, provision_pane,
)
from claudeteam.store import local_facts


# ── pane_env_prefix ───────────────────────────────────────────────


def test_pane_env_prefix_always_includes_state_dir():
    """Even with no other env set, STATE_DIR is always emitted so the
    spawned pane never falls back to ~/.claudeteam."""
    with isolated_env(team={"agents": {"a": {}}}):
        prefix = pane_env_prefix()
    assert prefix.startswith("CLAUDETEAM_STATE_DIR=")


def test_pane_env_prefix_propagates_lark_profile_when_set():
    with isolated_env(team={"agents": {"a": {}}}), env_patch(
            LARK_CLI_PROFILE="prod"):
        prefix = pane_env_prefix()
    assert "LARK_CLI_PROFILE=prod" in prefix


def test_pane_env_prefix_skips_unset_vars():
    """Vars not present in the operator shell don't pollute the prefix."""
    with isolated_env(team={"agents": {"a": {}}}), env_patch(
            LARK_CLI_PROFILE=None,
            LARK_CLI_NO_PROXY=None,
            CLAUDETEAM_LARK_SEND_AS=None,
            CLAUDETEAM_DEFAULT_MODEL=None):
        prefix = pane_env_prefix()
    # Only state_dir survives (team_file/runtime_config are set by isolated_env)
    assert "LARK_CLI_PROFILE=" not in prefix
    assert "LARK_CLI_NO_PROXY=" not in prefix


def test_pane_env_prefix_propagates_feishu_app_credentials():
    """REGRESSION: a tmux server started by an earlier checkout had its
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
        prefix = pane_env_prefix()
    assert "FEISHU_APP_ID=cli_NEW" in prefix
    assert "FEISHU_APP_SECRET=newSecret123" in prefix
    assert "LARKSUITE_CLI_APP_ID=cli_NEW" in prefix
    assert "LARKSUITE_CLI_APP_SECRET=newSecret123" in prefix


def test_pane_env_prefix_shell_quotes_paths_with_spaces():
    """shlex.quote should wrap any value containing whitespace; otherwise
    `eval $(...)` in a downstream shell would split on the space."""
    with isolated_env(team={"agents": {"a": {}}}), env_patch(
            LARK_CLI_PROFILE="my profile"):
        prefix = pane_env_prefix()
    # quoted form: 'my profile' (single quotes) — never raw `my profile`
    assert "'my profile'" in prefix


# ── provision_pane: LAZY ──────────────────────────────────────────


def test_provision_lazy_agent_sets_待命_and_skips_spawn():
    """Lazy agents in team.json get status 待命; spawn_agent is never
    called (the pane stays at a shell prompt)."""
    team = {"agents": {"sleepy": {"cli": "claude-code", "lazy": True, "runner": "tmux"}}}
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
    team = {"agents": {"alice": {"cli": "claude-code", "model": "opus", "runner": "tmux"}}}
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


def test_provision_sources_env_from_file_never_inline():
    """The spawn command sent to the pane must SOURCE the env file, not
    carry `KEY=value` inline — otherwise secrets (FEISHU_APP_SECRET,
    OPENAI_API_KEY) end up in the pane scrollback + the agent's context."""
    team = {"agents": {"a": {"cli": "claude-code", "runner": "tmux"}}}
    spawn_calls = []
    with isolated_env(team=team), tmux_patch(
            spawn_agent=lambda t, c: spawn_calls.append((str(t), c)) or True,
            inject=lambda *a, **kw: True), \
            attr_patch(wake, wait_until_ready=lambda *a, **kw: True):
        provision_pane("a", tmux.Target("S", "a"))
        cmd = spawn_calls[0][1]
        # env is sourced, NOT a visible KEY=value prefix
        assert "CLAUDETEAM_STATE_DIR=" not in cmd
        assert cmd.startswith(". ") and "spawn-env/a.sh" in cmd
        assert "claude" in cmd  # adapter's real CLI spawn follows
        # the actual env lives in a private file, off the wire
        envfile = paths.state_dir() / "spawn-env" / "a.sh"
        assert "CLAUDETEAM_STATE_DIR=" in envfile.read_text()


def test_build_spawn_command_keeps_secrets_off_the_wire():
    """build_spawn_command must keep the agent credential out of the
    returned command string and write it to a mode-0600 file instead."""
    import stat
    from claudeteam.agents.base import AuthSlots

    class _ApiKeyAdapter:
        def auth_slots(self):
            return AuthSlots(token_env=None,
                             api_key_envs=("OPENAI_API_KEY",),
                             login_credfile=None)

    secret = "sk-DEADBEEF-not-a-real-key"
    with isolated_env(team={"agents": {"w": {"cli": "x"}}}), \
            env_patch(OPENAI_API_KEY=secret, FEISHU_APP_SECRET="feishu-shh"):
        cmd = build_spawn_command("w", _ApiKeyAdapter(), "mycli --go")
        assert secret not in cmd            # credential never inline
        assert "feishu-shh" not in cmd      # Feishu secret never inline
        assert cmd.startswith(". ") and cmd.endswith("&& mycli --go")
        envfile = paths.state_dir() / "spawn-env" / "w.sh"
        body = envfile.read_text()
        assert secret in body and "feishu-shh" in body
        mode = stat.S_IMODE(envfile.stat().st_mode)
        assert mode == 0o600, oct(mode)     # not group/world readable


# ── provision_pane: READY_NO_INIT ─────────────────────────────────


def test_provision_ready_no_init_when_marker_never_appears():
    """When wait_until_ready times out, spawn already happened so the
    pane is alive — status still flips to 进行中, but the identity
    init prompt is NOT injected (no point injecting into a CLI that
    might still be loading)."""
    team = {"agents": {"a": {"cli": "claude-code", "runner": "tmux"}}}
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


# ── provision_pane: CONFIG_ERROR ─────────────────────────────────


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


# ── _pick_claude_seed (docker login-loop fix) ────────────────────


def _write(tmp: Path, name: str, body: str) -> Path:
    p = tmp / name
    p.write_text(body)
    return p


def test_pick_claude_seed_prefers_account_over_stub():
    """The Dockerfile stub /root/.claude.json (no oauthAccount) sorts
    before the real /root/host-claude.json mount; the helper must skip
    the stub and pick the account-bearing file regardless of order."""
    import tempfile
    with tempfile.TemporaryDirectory() as d:
        tmp = Path(d)
        stub = _write(tmp, "stub.json", '{"firstStartTime":"x","userID":"u"}')
        full = _write(tmp, "full.json", '{"oauthAccount":{"emailAddress":"a@b"}}')
        # stub first in priority order — still must return the account file
        assert lifecycle._pick_claude_seed([stub, full]) == full.read_bytes()


def test_pick_claude_seed_returns_first_account_when_in_priority_slot():
    import tempfile
    with tempfile.TemporaryDirectory() as d:
        tmp = Path(d)
        full = _write(tmp, "full.json", '{"oauthAccount":{"emailAddress":"a@b"}}')
        stub = _write(tmp, "stub.json", '{"userID":"u"}')
        assert lifecycle._pick_claude_seed([full, stub]) == full.read_bytes()


def test_pick_claude_seed_falls_back_to_first_readable_when_no_account():
    """No source carries an account → still seed *something* so the
    onboarding/migration flags land (better than claude writing a bare
    stub of its own)."""
    import tempfile
    with tempfile.TemporaryDirectory() as d:
        tmp = Path(d)
        a = _write(tmp, "a.json", '{"userID":"first"}')
        b = _write(tmp, "b.json", '{"userID":"second"}')
        assert lifecycle._pick_claude_seed([a, b]) == a.read_bytes()


def test_pick_claude_seed_skips_unreadable_candidates():
    import tempfile
    with tempfile.TemporaryDirectory() as d:
        tmp = Path(d)
        missing = tmp / "nope.json"  # never created
        full = _write(tmp, "full.json", '{"oauthAccount":{"x":1}}')
        assert lifecycle._pick_claude_seed([missing, full]) == full.read_bytes()


def test_pick_claude_seed_none_when_nothing_readable():
    import tempfile
    with tempfile.TemporaryDirectory() as d:
        tmp = Path(d)
        assert lifecycle._pick_claude_seed(
            [tmp / "a.json", tmp / "b.json"]) is None


# ── _mark_project_trusted (folder-trust dialog) ──────────────────


def test_mark_project_trusted_adds_entry_and_preserves_account():
    """Fresh agent claude.json seeded from the host account has the
    operator's projects but not /data; marking must add the trust flag
    without dropping oauthAccount or the other projects."""
    import json
    import tempfile
    with tempfile.TemporaryDirectory() as d:
        cj = Path(d) / ".claude.json"
        cj.write_text(json.dumps({
            "oauthAccount": {"emailAddress": "a@b"},
            "projects": {"/home/me/proj": {"hasTrustDialogAccepted": True}},
        }))
        lifecycle._mark_project_trusted(cj, Path("/data"))
        out = json.loads(cj.read_text())
        assert out["projects"]["/data"]["hasTrustDialogAccepted"] is True
        assert out["oauthAccount"] == {"emailAddress": "a@b"}      # preserved
        assert "/home/me/proj" in out["projects"]                  # preserved


def test_mark_project_trusted_idempotent_no_clobber():
    """Second call must not wipe other fields claude wrote into the
    project entry (lastSessionId etc.)."""
    import json
    import tempfile
    with tempfile.TemporaryDirectory() as d:
        cj = Path(d) / ".claude.json"
        cj.write_text(json.dumps({
            "projects": {"/data": {"hasTrustDialogAccepted": True,
                                   "lastSessionId": "s1"}}}))
        lifecycle._mark_project_trusted(cj, Path("/data"))
        out = json.loads(cj.read_text())
        assert out["projects"]["/data"]["lastSessionId"] == "s1"


def test_mark_project_trusted_swallows_malformed_json():
    """A corrupt claude.json must never abort `claudeteam start`."""
    import tempfile
    with tempfile.TemporaryDirectory() as d:
        cj = Path(d) / ".claude.json"
        cj.write_text("{not valid json")
        lifecycle._mark_project_trusted(cj, Path("/data"))  # must not raise


# ── _ensure_claude_agent_home ────────────────────────────────────


def test_ensure_claude_agent_home_does_not_raise_when_data_missing():
    """On hosts without /data (macOS, test runners), the helper falls
    back to <state_dir>/agent-home/<agent> — don't crash claudeteam
    start outside Docker."""
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
    refresh, defeating the share intent — which surfaced an empty
    'refreshToken: ""'. Pin the regular-file invariant."""
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
        from claudeteam.runtime.paths import agent_home
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
        from claudeteam.runtime.paths import agent_home
        cred = Path(agent_home("manager")) / ".claude" / ".credentials.json"
        assert "v1-tok" in cred.read_text()
        lifecycle._ensure_claude_agent_home("manager")
        # Second call must replace the file with v2's content
        assert "v2-tok" in cred.read_text(), \
            "stale snapshot not overwritten on re-provision"


# ── _ensure_agent_home (wave2 A: per-agent HOME for every CLI) ────


def test_ensure_agent_home_creates_dir_for_non_claude_cli():
    """Non-claude CLIs (gemini/qwen/kimi/codex) get their per-agent HOME
    root created so the spawn's `HOME=<agent_home>` has a real isolated
    dir to land config / cache / native-memory into, instead of racing
    the shared operator HOME across panes."""
    team = {"agents": {"worker_gem": {"cli": "gemini-cli"}}}
    with isolated_env(team=team):
        lifecycle._ensure_agent_home("worker_gem", "gemini-cli")
        home = Path(paths.agent_home("worker_gem"))
        assert home.is_dir(), "per-agent HOME not created for non-claude CLI"


def test_ensure_agent_home_dispatches_claude_to_full_seeding():
    """claude-code routes through `_ensure_claude_agent_home` (full
    keychain/settings/trust seeding); every other CLI must NOT — it only
    needs the home dir. Pin the dispatch so a future adapter doesn't
    accidentally inherit claude's keychain provisioning path."""
    calls = []
    with attr_patch(lifecycle, _ensure_claude_agent_home=calls.append):
        lifecycle._ensure_agent_home("manager", "claude-code")
        lifecycle._ensure_agent_home("worker_gem", "gemini-cli")
    assert calls == ["manager"], \
        f"expected claude-only delegation to full seeding, got {calls}"


def test_ensure_agent_home_non_claude_never_raises_on_unwritable_path():
    """Best-effort contract: an unwritable agent_home must be swallowed,
    not bubbled into provision_pane — the init-prompt memory injection is
    still a working fallback even when the native HOME can't be made."""
    with attr_patch(paths, agent_home=lambda a: "/proc/cannot/mkdir/here"):
        # Must not raise despite the unwritable target.
        lifecycle._ensure_agent_home("worker_gem", "gemini-cli")


# ── provision_pane: codex trust → per-agent CODEX_HOME (wave2 B) ──


def test_provision_codex_trusts_workdir_in_per_agent_config():
    """REGRESSION: codex trust used to write the shared
    ~/.codex/config.toml, so per-agent CODEX_HOME isolation would leave
    the trust entry in the wrong file → first-run trust prompt blocks the
    pane. Trust must now land in <agent_home>/.codex/config.toml."""
    from claudeteam.agents.codex_cli import codex_home
    team = {"agents": {"worker_codex": {"cli": "codex-cli", "runner": "tmux"}}}
    captured = []
    with isolated_env(team=team), tmux_patch(
            spawn_agent=lambda t, c: True,
            inject=lambda *a, **kw: True), \
            attr_patch(wake, wait_until_ready=lambda *a, **kw: True), \
            attr_patch(lifecycle,
                       ensure_workdir_trusted=lambda wd, config_path=None:
                       captured.append(config_path)):
        provision_pane("worker_codex", tmux.Target("S", "worker_codex"))
        # codex_home resolves against the isolated state_dir, so compute the
        # expected path INSIDE the env block.
        assert captured, "codex trust was never invoked"
        assert captured[0] == Path(codex_home("worker_codex")) / "config.toml"


# ── _seed_cli_credentials (wave2 H: keep isolated HOMEs logged in) ─
# HOME isolation strands codex/gemini/qwen at a fresh, logged-out state
# dir. Seed copies the operator's OAuth file into the isolated HOME so the
# CLI stays authenticated. kimi is NOT isolated (Plan B keeps its HOME on
# the operator) so it's never seeded.


def _operator_cred(tmp: Path, rel: str, body: str) -> Path:
    """Create a fake operator-side credential file under tmp/operhome and
    return that HOME dir (caller wraps the seed call in env_patch(HOME=…))."""
    oper = tmp / "operhome"
    src = oper / rel
    src.parent.mkdir(parents=True, exist_ok=True)
    src.write_text(body)
    return oper


def test_seed_symlinks_codex_oauth_into_isolated_home():
    """The agent's isolated CODEX_HOME gets a SYMLINK to the operator's
    auth.json (not a copy), so an OAuth token refresh on the one shared file
    propagates to every agent instead of drifting per agent."""
    team = {"agents": {"worker_codex": {"cli": "codex-cli"}}}
    with isolated_env(team=team) as tmp:
        oper = _operator_cred(tmp, ".codex/auth.json", '{"tokens":"oauth"}')
        with env_patch(HOME=str(oper)):
            lifecycle._seed_cli_credentials("worker_codex", "codex-cli")
        dst = Path(paths.agent_home("worker_codex")) / ".codex" / "auth.json"
        assert dst.is_symlink(), "expected a symlink, not a copy"
        assert dst.read_text() == '{"tokens":"oauth"}'
        # a refresh on the shared file is seen through the link (the whole point)
        (oper / ".codex" / "auth.json").write_text('{"tokens":"refreshed"}')
        assert dst.read_text() == '{"tokens":"refreshed"}'


def test_seed_resolves_package_name_alias():
    """team.json may use the `qwen-cli` alias; seeding keys on the
    adapter's process_name so the alias maps to the same qwen entry."""
    team = {"agents": {"worker_qwen": {"cli": "qwen-cli"}}}
    with isolated_env(team=team) as tmp:
        oper = _operator_cred(tmp, ".qwen/oauth_creds.json", '{"q":1}')
        with env_patch(HOME=str(oper), OPENAI_API_KEY=None):
            lifecycle._seed_cli_credentials("worker_qwen", "qwen-cli")
        dst = Path(paths.agent_home("worker_qwen")) / ".qwen" / "oauth_creds.json"
        assert dst.read_text() == '{"q":1}'


def test_seed_skips_kimi_not_home_isolated():
    """kimi keeps cwd=repo and inherits the operator HOME (no
    HOME=<agent_home> in its spawn_cmd), so it already sees ~/.kimi —
    nothing is copied into a (would-be) isolated home."""
    team = {"agents": {"worker_kimi": {"cli": "kimi-cli"}}}
    with isolated_env(team=team) as tmp:
        oper = _operator_cred(tmp, ".kimi/config.toml", "token=x")
        with env_patch(HOME=str(oper)):
            lifecycle._seed_cli_credentials("worker_kimi", "kimi-cli")
        dst = Path(paths.agent_home("worker_kimi")) / ".kimi" / "config.toml"
        assert not dst.exists()


def test_seed_skips_gemini_when_api_key_set():
    """An API key authenticates gemini directly → no OAuth file to seed."""
    team = {"agents": {"worker_gem": {"cli": "gemini-cli"}}}
    with isolated_env(team=team) as tmp:
        oper = _operator_cred(tmp, ".gemini/oauth_creds.json", '{"g":1}')
        with env_patch(HOME=str(oper), GEMINI_API_KEY="ai-key"):
            lifecycle._seed_cli_credentials("worker_gem", "gemini-cli")
        dst = Path(paths.agent_home("worker_gem")) / ".gemini" / "oauth_creds.json"
        assert not dst.exists()


def test_seed_gemini_copies_when_no_api_key():
    team = {"agents": {"worker_gem": {"cli": "gemini-cli"}}}
    with isolated_env(team=team) as tmp:
        oper = _operator_cred(tmp, ".gemini/oauth_creds.json", '{"g":1}')
        with env_patch(HOME=str(oper), GEMINI_API_KEY=None):
            lifecycle._seed_cli_credentials("worker_gem", "gemini-cli")
        dst = Path(paths.agent_home("worker_gem")) / ".gemini" / "oauth_creds.json"
        assert dst.read_text() == '{"g":1}'


def test_seed_silent_when_source_absent():
    """Operator never logged this CLI in → no source file → silent skip."""
    team = {"agents": {"worker_codex": {"cli": "codex-cli"}}}
    with isolated_env(team=team) as tmp:
        with env_patch(HOME=str(tmp / "empty_home")):
            lifecycle._seed_cli_credentials("worker_codex", "codex-cli")  # no raise
        dst = Path(paths.agent_home("worker_codex")) / ".codex" / "auth.json"
        assert not dst.exists()


def test_seed_does_not_clobber_existing_dst():
    """A per-agent token already refreshed on disk must win over the
    (older) operator copy — never overwrite an existing dest."""
    team = {"agents": {"worker_codex": {"cli": "codex-cli"}}}
    with isolated_env(team=team) as tmp:
        dst = Path(paths.agent_home("worker_codex")) / ".codex" / "auth.json"
        dst.parent.mkdir(parents=True, exist_ok=True)
        dst.write_text("REFRESHED")
        oper = _operator_cred(tmp, ".codex/auth.json", "OPERATOR")
        with env_patch(HOME=str(oper)):
            lifecycle._seed_cli_credentials("worker_codex", "codex-cli")
        assert dst.read_text() == "REFRESHED"


def test_seed_codex_leaves_config_toml_untouched():
    """codex's per-agent config.toml (written by ensure_workdir_trusted)
    sits beside auth.json — seeding the credential must never rewrite it."""
    from claudeteam.agents.codex_cli import codex_home
    team = {"agents": {"worker_codex": {"cli": "codex-cli"}}}
    with isolated_env(team=team) as tmp:
        cdir = Path(codex_home("worker_codex"))
        cdir.mkdir(parents=True, exist_ok=True)
        (cdir / "config.toml").write_text('[projects."/x"]\n')
        oper = _operator_cred(tmp, ".codex/auth.json", "AUTH")
        with env_patch(HOME=str(oper)):
            lifecycle._seed_cli_credentials("worker_codex", "codex-cli")
        assert (cdir / "config.toml").read_text() == '[projects."/x"]\n'
        assert (cdir / "auth.json").read_text() == "AUTH"


def test_seed_unknown_cli_swallowed():
    """A bad `cli` value resolves to no adapter → KeyError swallowed, no
    raise (mirrors the rest of provisioning's best-effort contract)."""
    lifecycle._seed_cli_credentials("ghost", "not-a-cli")  # must not raise


def test_ensure_agent_home_seeds_codex_oauth():
    """End-to-end: the non-claude branch of _ensure_agent_home both makes
    the home dir AND seeds the credential."""
    team = {"agents": {"worker_codex": {"cli": "codex-cli"}}}
    with isolated_env(team=team) as tmp:
        oper = _operator_cred(tmp, ".codex/auth.json", "AUTH")
        with env_patch(HOME=str(oper)):
            lifecycle._ensure_agent_home("worker_codex", "codex-cli")
        dst = Path(paths.agent_home("worker_codex")) / ".codex" / "auth.json"
        assert dst.read_text() == "AUTH"
