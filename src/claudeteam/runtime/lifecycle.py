"""Pane provisioning shared between `start` and `hire`.

`provision_pane(agent, target)` writes identity, handles lazy panes,
spawns the configured CLI, waits for the ready banner, injects the
identity init prompt, and updates the agent's status row. Both
`commands/start.py` (looping over the team) and `commands/hire.py`
(single agent) call into this so the spawn-and-init contract lives in
one place.

Returns one of five outcome strings (callers render differently):
  LAZY            agent has `lazy: true` in team.json; no spawn attempted,
                  status set to 待命
  READY           CLI spawned + ready marker seen + identity init injected
  READY_NO_INIT   CLI spawned but ready marker didn't appear in 20s;
                  identity init skipped (caller surfaces a warning)
  SPAWN_FAILED    `tmux.spawn_agent` returned False (tmux send-keys failed)
  CONFIG_ERROR    bad `cli` value (typo, dropped adapter) caught as
                  KeyError on adapter lookup; caller logs + skips this
                  agent, keeps going for the rest of the team rather
                  than aborting the whole `claudeteam start`.

Also home for `build_spawn_command()` — wraps an adapter's spawn_cmd so the
pane inherits `CLAUDETEAM_STATE_DIR`, the Feishu env, and the agent's
credential by SOURCING a private mode-0600 file (written by `pane_env_prefix`
+ agent_auth), instead of typing `KEY=secret` into the pane where it would
leak into the scrollback and the agent's own context.
"""
from __future__ import annotations

import json
import os
import shlex
from pathlib import Path

from claudeteam.agents import get_adapter, identity
from claudeteam.agents.codex_cli import ensure_workdir_trusted
from claudeteam.runtime import config, paths, tmux, wake
from claudeteam.store import local_facts
from claudeteam.util import env_str


# env vars to propagate from the operator's shell into every spawned pane
# so worker agents' shell-out calls (via Bash tool) see the deployment's
# state dir instead of re-deriving a different one from the pane's own cwd.
#
# FEISHU_APP_*/LARKSUITE_CLI_APP_* are propagated too: when the
# tmux server was started by an earlier checkout's `claudeteam up`, new
# panes inherit *its* global env (no FEISHU_APP_ID/SECRET). lark.py's
# tenant_token_from_env() returned None and fell back to the saved
# lark-cli profile — a different app — yielding HTTP 400 "Bot/User can
# NOT be out of the chat" on every `claudeteam say`. Embedding the creds
# in the spawn-cmd prefix sidesteps the tmux-server-env quirk entirely.
_PROPAGATED_ENV = (
    "LARK_CLI_PROFILE",
    "LARK_CLI_NO_PROXY",
    "CLAUDETEAM_LARK_SEND_AS",
    "CLAUDETEAM_TEAM_FILE",
    "CLAUDETEAM_RUNTIME_CONFIG",
    "CLAUDETEAM_DEFAULT_MODEL",
    # The OpenAI-compatible endpoint the worker CLIs point at (DeepSeek/OpenAI/
    # a local server/…). Deployment config, not a credential, so it rides here;
    # the per-agent API KEY goes through agent_auth instead. Propagated so panes
    # see it reliably even off a stale tmux-server env.
    "OPENAI_BASE_URL",
    "FEISHU_APP_ID",
    "FEISHU_APP_SECRET",
    "LARKSUITE_CLI_APP_ID",
    "LARKSUITE_CLI_APP_SECRET",
)


def _path_readable(p: Path) -> bool:
    """Returns True iff `p` can be stat'd. False on PermissionError /
    not-found / any OSError. On a Linux host where /root is mode 700,
    Path("/root/...").exists() raised PermissionError instead of
    returning False (Python <3.13 behavior), killing `claudeteam up`
    for non-root deployers. Three /root probes in this module need the
    soft semantic."""
    try:
        return p.exists()
    except OSError:
        return False


def _pick_claude_seed(candidates: list[Path]) -> bytes | None:
    """Choose the bytes to seed a fresh agent ~/.claude.json with.

    claude needs the `oauthAccount` key here or it pops the OAuth login
    dialog (credentials.json alone isn't enough). Walk `candidates` in
    priority order and return the first that carries an account; if none
    do, return the first readable one anyway so onboarding/migration
    flags still land. None when nothing is readable.

    The Dockerfile's `claude --version` leaves a stub /root/.claude.json
    with no oauthAccount. Seeding from it blind (it sorts before the real
    /root/host-claude.json mount) put every agent at the login screen
    despite a valid CLAUDE_CODE_OAUTH_TOKEN.
    """
    seed = None
    for src in candidates:
        if not _path_readable(src):
            continue
        try:
            data = src.read_bytes()
        except OSError:
            continue
        if seed is None:
            seed = data
        if b'"oauthAccount"' in data:
            return data
    return seed


def _mark_project_trusted(claude_json: Path, workdir: Path) -> None:
    """Pre-accept claude's per-folder trust dialog for `workdir` by
    setting projects[workdir].hasTrustDialogAccepted in the agent's
    ~/.claude.json. The seed source (host account) only lists the
    operator's own project paths, so a fresh container agent-home blocks
    at the interactive "Is this a project you trust?" gate on first
    spawn — which stalls wait_until_ready and skips the identity init
    prompt. Mirrors codex's ensure_workdir_trusted. Idempotent +
    best-effort: a malformed claude.json or read-only home never aborts
    `claudeteam start`.
    """
    key = str(workdir)
    try:
        data = json.loads(claude_json.read_text())
    except (OSError, ValueError):
        return
    if not isinstance(data, dict):
        return
    entry = data.setdefault("projects", {}).setdefault(key, {})
    if entry.get("hasTrustDialogAccepted") is True:
        return  # already trusted — skip the (148 KB) rewrite
    entry["hasTrustDialogAccepted"] = True
    try:
        claude_json.write_text(json.dumps(data))
    except OSError:
        pass


def _ensure_claude_agent_home(agent: str) -> None:
    """Materialise a per-agent claude state dir at /data/agent-home/<agent>.

    Each claude pane spawns with `HOME=/data/agent-home/<agent>` so
    each agent has its own `~/.claude.json` (avoids the shared-file
    write-race that corrupts a single-mount setup). The directory
    contains:
      .claude/settings.json     — silent-launch flags (theme, perms)
      .claude/.credentials.json — symlink to /root/.claude/.credentials.json
                                  so OAuth tokens stay bind-mount shared
      .claude/projects          — symlink to /root/.claude/projects
                                  so ccusage in /usage finds session logs
    Best-effort: if /data isn't writable (host tests where the path
    doesn't exist), silently skip and let claude fall back to its
    default `$HOME` discovery.
    """
    from claudeteam.runtime.paths import agent_home as _agent_home
    home = Path(_agent_home(agent))
    claude_dir = home / ".claude"
    try:
        claude_dir.mkdir(parents=True, exist_ok=True)
    except OSError:
        return
    # Host fallback: claude on macOS keys keychain lookup by $HOME, so a
    # per-agent HOME with no .credentials.json gets "Not logged in" even
    # though the keychain entry exists for the user. Export it to a file
    # the first time so each pane has working OAuth.
    cred_link = claude_dir / ".credentials.json"
    # macOS host: prefer the live keychain over a (potentially-stale) host
    # ~/.claude/.credentials.json. Claude refreshes OAuth into the keychain
    # but only writes the file occasionally, so a symlink to the host file
    # can hand the pane a `refreshToken` the server has already revoked:
    # a pane symlinked to the stale host file would round-trip a 401,
    # claude blanked the field, and the pane logged "401 Invalid auth
    # credentials". Re-extract on every provision and write
    # a *regular file* — not a symlink — because claude's atomic-write
    # of credentials replaces the symlink target with a plain file on
    # first refresh anyway, defeating the original sharing intent.
    import platform
    keychain_extracted = False
    if platform.system() == "Darwin":
        import subprocess
        try:
            out = subprocess.run(
                ["security", "find-generic-password",
                 "-s", "Claude Code-credentials", "-w"],
                capture_output=True, text=True, timeout=5,
            )
            if out.returncode == 0 and out.stdout.strip():
                if cred_link.is_symlink() or cred_link.exists():
                    cred_link.unlink()
                cred_link.write_text(out.stdout)
                keychain_extracted = True
        except (OSError, subprocess.TimeoutExpired):
            # `security` missing / keychain locked / subprocess timeout →
            # silent skip and fall through to the host-file branch below.
            pass
    # Docker/Linux: the shared /root/.claude/.credentials.json is bind-mounted
    # and the watchdog rotates its OAuth token — SYMLINK the per-agent file to it
    # so a refresh reaches every pane (a copy goes stale the moment the watchdog
    # rotates the shared token; that was the bug). macOS took the keychain branch
    # above (where /root/.claude isn't readable), so this is a no-op there.
    cred_target = Path("/root/.claude/.credentials.json")
    if not keychain_extracted and not cred_link.exists() and _path_readable(cred_target):
        try:
            cred_link.symlink_to(cred_target)
        except OSError:
            pass
    if not keychain_extracted and not cred_link.exists():
        user_creds = Path.home() / ".claude" / ".credentials.json"
        if user_creds.exists():
            try:
                # Copy — last resort (no keychain, no shared target). claude's
                # atomic-write replaces a symlink with a plain file on refresh,
                # so a plain file is the honest starting point here.
                cred_link.write_bytes(user_creds.read_bytes())
            except OSError:
                pass
    settings = claude_dir / "settings.json"
    if not settings.exists():
        settings.write_text(
            '{\n'
            '  "skipDangerousModePermissionPrompt": true,\n'
            '  "hasCompletedOnboarding": true,\n'
            '  "theme": "dark",\n'
            '  "permissions": {\n'
            '    "allow": ["Bash", "Edit", "Read", "Write"]\n'
            '  }\n'
            '}\n'
        )
    projects_link = claude_dir / "projects"
    projects_target = Path("/root/.claude/projects")
    if _path_readable(projects_target) and not projects_link.exists():
        try:
            projects_link.symlink_to(projects_target)
        except OSError:
            pass
    # Seed ~/.claude.json once. Without the `oauthAccount` + `userID`
    # keys claude pops the OAuth login dialog (credentials.json alone
    # isn't enough — claude checks ~/.claude.json for "login complete"
    # state). Two candidate sources, in priority order: the explicit
    # Docker mount (/root/host-claude.json) and the invoking user's own
    # ~/.claude.json (host deployment). The Dockerfile's `claude
    # --version` leaves a stub /root/.claude.json with no oauthAccount,
    # so prefer whichever source actually carries an account; only fall
    # back to an account-less file if that's all we have. After the copy
    # the per-agent file is writable so claude can update its own
    # session counters without affecting other agents.
    claude_json = home / ".claude.json"
    if not claude_json.exists():
        seed = _pick_claude_seed(
            [Path("/root/host-claude.json"), Path.home() / ".claude.json"])
        if seed is not None:
            try:
                claude_json.write_bytes(seed)
            except OSError:
                pass
    # Pre-trust the pane's working dir so claude doesn't block on the
    # folder-trust dialog. Path.cwd() is the spawn cwd inherited by the
    # tmux pane (same assumption as codex's ensure_workdir_trusted).
    if claude_json.exists():
        _mark_project_trusted(claude_json, Path.cwd())


# Per-CLI OAuth/credential file to SYMLINK from the operator HOME into the
# agent's isolated HOME so HOME isolation doesn't log the CLI out — and so a
# token refresh propagates to the one shared file (no per-agent drift).
# Keyed by the adapter's process_name() so the package-name aliases
# (kimi-cli / qwen-cli) collapse onto one entry. Value:
#   (rel  — path of the cred file under HOME, identical on both sides,
#    skip_env — env var whose presence means the CLI authenticates by API
#               key and needs no seeded OAuth file; None = always seed)
#
# claude-code is absent — its richer seeding (macOS keychain, ~/.claude.json,
# folder trust) lives in _ensure_claude_agent_home. kimi is absent on
# purpose: its adapter does NOT set HOME=<agent_home> (it keeps cwd=repo
# with no native file), so the pane already inherits the operator's
# ~/.kimi/config.toml — there is nothing to isolate and nothing to seed.
# (In the prod container, worker_kimi has no agent-home at all.) If kimi
# ever gains HOME isolation, add its seed entry here.
_CLI_CRED_SEEDS: dict[str, tuple[str, str | tuple[str, ...] | None]] = {
    "codex":  (".codex/auth.json", None),
    "gemini": (".gemini/oauth_creds.json", "GEMINI_API_KEY"),
    "qwen":   (".qwen/oauth_creds.json", ("DASHSCOPE_API_KEY", "OPENAI_API_KEY")),
}


def _seed_cli_credentials(agent: str, cli: str) -> None:
    """Symlink the operator's OAuth credential file for `cli` into the agent's
    isolated HOME, so the per-agent `HOME=<agent_home>` (codex: `CODEX_HOME`)
    doesn't strand the CLI at a fresh, logged-out state dir — and a token
    refresh propagates to the one shared file instead of drifting per agent.

    Best-effort throughout — any of these silently skips, never aborting the
    provision:
      • CLI not in `_CLI_CRED_SEEDS` (claude handled elsewhere; kimi not
        isolated)
      • the skip-env is set (API-key auth → OAuth file unnecessary)
      • the operator source file is absent / unreadable (never logged in)
      • the dest already exists (don't clobber a refreshed per-agent token)
      • the dest HOME isn't writable

    Only the credential file is touched: codex's per-agent config.toml
    (written by `ensure_workdir_trusted`) lives beside auth.json and is
    never overwritten here."""
    try:
        adapter = get_adapter(cli)
    except KeyError:
        return
    spec = _CLI_CRED_SEEDS.get(adapter.process_name())
    if spec is None:
        return
    rel, skip_env = spec
    # Skip seeding the OAuth file when the operator authenticates by API key —
    # check ALL of the CLI's key vars (qwen: DASHSCOPE *and* OPENAI). Otherwise we
    # symlink an OAuth file that makes agent_auth resolve 'login' and blank the key
    # the operator actually set.
    skip_envs = (skip_env,) if isinstance(skip_env, str) else (skip_env or ())
    if any(env_str(e) for e in skip_envs):
        return
    from claudeteam.runtime.paths import agent_home as _agent_home
    src = Path.home() / rel
    dst = Path(_agent_home(agent)) / rel
    if dst.exists() or dst.is_symlink() or not _path_readable(src):
        return
    try:
        dst.parent.mkdir(parents=True, exist_ok=True)
        # Symlink, NOT copy: the agent reads the ONE shared credential file, so
        # an OAuth token refresh propagates to every agent and the operator —
        # no per-agent drift, no rotating-refresh logout. (Matches multica's
        # codex handling.) claude is the deliberate exception: it atomic-writes
        # credentials, which would replace this link with a stale private file
        # → 401, so it is copied in _ensure_claude_agent_home instead.
        dst.symlink_to(src)
    except OSError:
        pass


def _ensure_agent_home(agent: str, cli: str) -> None:
    """Provision the per-agent HOME for any CLI before spawn.

    claude-code gets the full seeding (keychain creds, settings.json,
    ~/.claude.json, folder trust) via `_ensure_claude_agent_home`. Every
    other CLI just needs its per-agent HOME *directory* to exist so the
    spawn's `HOME=<agent_home>` (and codex's `CODEX_HOME`) lands the CLI's
    own config / cache / native-memory file in an isolated dir instead of
    racing the operator HOME across panes. The native memory file itself
    (AGENTS.md / GEMINI.md / QWEN.md) is written by `identity.write` via
    `atomic_write_text`, which creates its own parent dir — so all we owe
    here is the home root.

    For the HOME-isolated non-claude CLIs we additionally seed the
    operator's OAuth credential into the isolated HOME (see
    `_seed_cli_credentials`), otherwise the fresh state dir would leave the
    CLI logged out.

    Best-effort: an unwritable path must not fail the whole provision (the
    init-prompt memory injection still delivers identity + digest)."""
    if cli == "claude-code":
        _ensure_claude_agent_home(agent)
        return
    from claudeteam.runtime.paths import agent_home as _agent_home
    try:
        Path(_agent_home(agent)).mkdir(parents=True, exist_ok=True)
    except OSError:
        pass
    _seed_cli_credentials(agent, cli)


def pane_env_prefix() -> str:
    """Build a shell env prefix that, prepended to a spawn_cmd, makes the
    spawned process inherit CLAUDETEAM_STATE_DIR and the Feishu env so
    worker agents calling `claudeteam say` write to the team's state dir —
    the resolved path is passed explicitly so the pane never re-derives a
    different one from its own cwd.
    """
    parts = [f"CLAUDETEAM_STATE_DIR={shlex.quote(str(paths.state_dir()))}"]
    for var in _PROPAGATED_ENV:
        val = env_str(var)
        if val:
            parts.append(f"{var}={shlex.quote(val)}")
    return " ".join(parts)


def _write_spawn_env_file(agent: str, assignments: str) -> Path | None:
    """Write `assignments` (a `K=v K2=v2` shell-assignment string, secrets
    and all) to a private mode-0600 file as a single `export …` line, so the
    pane can `source` it instead of having the secrets typed in via send-keys.

    Returns the file path, or None if the write fails — caller falls back to
    the inline prefix so a disk hiccup never blocks a spawn.
    """
    path = paths.state_dir() / "spawn-env" / f"{agent}.sh"
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        # os.open with 0o600 so the secret file is never group/world-readable,
        # not even for the window between create and a follow-up chmod.
        fd = os.open(str(path), os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
        with os.fdopen(fd, "w") as fh:
            fh.write(f"export {assignments}\n")
        os.chmod(path, 0o600)  # O_CREAT honours umask; re-assert if pre-existing
        return path
    except OSError:
        return None


def build_spawn_command(agent: str, adapter, spawn_cmd: str) -> str:
    """Wrap `spawn_cmd` so the pane gets its env (per-agent credential +
    propagated Feishu/state vars) by SOURCING a private mode-0600 file the
    parent writes — never by typing `KEY=secret` into the pane.

    The old `f"{spawn_env_prefix} {pane_env_prefix} {spawn_cmd}"` form was sent
    verbatim through tmux send-keys, so the agent's OPENAI_API_KEY and the
    deployment's FEISHU_APP_SECRET landed in the pane scrollback AND in the
    agent's own LLM context. Sourcing keeps secrets off the wire. Bonus: the
    sourced vars are `export`ed for the WHOLE command, so adapters that read
    `$OPENAI_API_KEY` in a later `&&` clause (codewhale, hermes, trae) see it —
    an inline `K=v cmd1 && cmd2` prefix only set K for cmd1.

    Falls back to the inline prefix if the file can't be written (degraded but
    never blocks a spawn).
    """
    from claudeteam.runtime import agent_auth
    assignments = (f"{agent_auth.spawn_env_prefix(agent, adapter)} "
                   f"{pane_env_prefix()}").strip()
    if not assignments:
        return spawn_cmd
    envfile = _write_spawn_env_file(agent, assignments)
    if envfile is None:
        return f"{assignments} {spawn_cmd}"
    return f". {shlex.quote(str(envfile))} && {spawn_cmd}"


# Outcome strings returned by provision_pane. Callers print/log differently
# (start uses loop-style "  → spawned", hire uses "✅ hired") so the helper
# stays I/O-free and lets the caller render.
LAZY = "lazy"
READY = "ready"
READY_NO_INIT = "ready_no_init"
SPAWN_FAILED = "spawn_failed"
CONFIG_ERROR = "config_error"


def provision_headless(agent: str) -> str:
    """Provision an ACP agent with NO tmux at all (Windows native / a
    server without tmux): identity + workspace + agent home + status.
    Everything the AcpHost needs; only the cosmetic viewer is missing.
    Always READY."""
    cfg = config.load_team().get("agents", {}).get(agent, {})
    cli = cfg.get("cli", "claude-code")
    model = (cfg.get("model") or env_str("CLAUDETEAM_DEFAULT_MODEL")
             or config.load_team().get("default_model", "opus"))
    identity.write(agent, role=cfg.get("role") or agent, cli=cli, model=model)
    paths.agent_workspace(agent).mkdir(parents=True, exist_ok=True)
    _ensure_agent_home(agent, cli)
    local_facts.upsert_status(agent, "待命", "acp: session starts on first message")
    return READY


def _provision_acp_viewer(agent: str, target: tmux.Target) -> str:
    """Provision an ACP agent: its CLI runs as a subprocess of the router's
    AcpHost, NOT in this pane — the pane becomes a read-only viewer tailing
    the agent's transcript so the operator keeps the "watch every employee
    work" tmux experience. The identity init turn, real session spawn, and
    status transitions all happen host-side on first prompt.

    Provision itself only (a) ensures the transcript exists so tail -F has
    a target, (b) starts the tail, (c) marks the agent 待命. Always READY —
    an ACP agent needs no ready-marker wait (there's no TUI to boot)."""
    from claudeteam.runtime.acp_host import transcript_file
    tf = transcript_file(agent)
    try:
        tf.parent.mkdir(parents=True, exist_ok=True)
        tf.touch(exist_ok=True)
    except OSError:
        pass
    _ensure_agent_home(agent, config.agent_cli(agent))
    viewer = (f"clear; echo '👁  {agent} · ACP runner · read-only transcript "
              f"(agent runs inside router)'; "
              f"tail -n 200 -F {shlex.quote(str(tf))}")
    if not tmux.spawn_agent(target, viewer):
        # The viewer is cosmetic — the agent itself lives in the router and
        # works fine without it. Warn, never fail the provision over it.
        import sys
        print(f"  ⚠️ {agent}: viewer pane didn't start (agent unaffected; "
              f"`claudeteam peek {agent}` still works)", file=sys.stderr)
    local_facts.upsert_status(agent, "待命", "acp: session starts on first message")
    return READY


def provision_pane(agent: str, target: tmux.Target) -> str:
    """Provision a freshly-created pane for `agent`.

    Pre-conditions: tmux window for `target` already exists and is empty
    (a shell prompt). Caller is responsible for window creation.

    Steps:
      1. Render + persist agent's identity.md (`agents/<name>/identity.md`).
      2. If agent is `lazy` in team.json: set status 待命, return LAZY.
      3. For codex CLI: ensure cwd is trusted in ~/.codex/config.toml.
      4. Spawn the adapter's CLI in the pane (env sourced via
         build_spawn_command, not typed in as a visible prefix).
      5. Wait up to 20s for the adapter's ready marker to appear.
      6. Inject the identity init prompt so the agent reads identity.md
         and reports for duty.
      7. Set status 进行中.

    Returns one of:
      LAZY            — status set to 待命, no CLI spawn attempted
      READY           — CLI spawned + identity init injected
      READY_NO_INIT   — CLI spawned but ready marker didn't appear in 20s
      SPAWN_FAILED    — tmux.spawn_agent returned False
      CONFIG_ERROR    — agent's `cli` value isn't registered (typo /
                        missing adapter); caller should warn + continue
                        with the rest of the team, NOT kill the whole start.
    """
    # Load team config once. start.py loops over N agents calling this
    # helper, so paying 3-4 disk reads here per agent (one for cfg, one
    # for adapter resolution, one for model fallback) compounds. Cache
    # locally and derive cfg / cli / model from the same dict.
    team = config.load_team()
    cfg = team.get("agents", {}).get(agent)
    if cfg is None:
        import sys
        print(f"  ⚠️ {agent}: agent {agent!r} not in claudeteam.toml", file=sys.stderr)
        return CONFIG_ERROR
    cli = cfg.get("cli", "claude-code")
    # Inline agent_model resolution: per-agent override → env var →
    # team default → "opus". Mirrors `config.agent_model` but uses the
    # already-loaded `team` dict for the default_model fallback.
    model = (cfg.get("model")
             or env_str("CLAUDETEAM_DEFAULT_MODEL")
             or team.get("default_model", "opus"))
    # Pass resolved fields to identity.write so its internal render()
    # skips a redundant config.agent_config() fallback. `role`
    # defaulting to `agent` matches render's own fallback so the
    # rendered file is byte-identical.
    identity.write(agent, role=cfg.get("role") or agent, cli=cli, model=model)
    # Each agent owns a private scratch dir for long reports / drafts so
    # output doesn't collide in the shared repo cwd (see the workspace
    # section that identity.render injects).
    paths.agent_workspace(agent).mkdir(parents=True, exist_ok=True)
    if config.agent_runner(agent) == "acp":
        return _provision_acp_viewer(agent, target)
    if cfg.get("lazy"):
        local_facts.upsert_status(agent, "待命", "lazy: CLI starts on first message")
        return LAZY
    _ensure_agent_home(agent, cli)
    if cli == "codex-cli":
        from claudeteam.agents.codex_cli import codex_home
        ensure_workdir_trusted(
            Path.cwd(), config_path=Path(codex_home(agent)) / "config.toml")
    try:
        adapter = get_adapter(cli)
    except KeyError as e:
        # Bad `cli` value in team.json — typo, dropped adapter, etc. One
        # bad agent shouldn't kill `claudeteam start` for the rest of
        # the team. Caller logs + skips.
        import sys
        print(f"  ⚠️ {agent}: {e}", file=sys.stderr)
        return CONFIG_ERROR
    cmd = build_spawn_command(agent, adapter, adapter.spawn_cmd(agent, model))
    if not tmux.spawn_agent(target, cmd):
        return SPAWN_FAILED
    # 60s ready timeout (was 20s): fresh container claude panes go
    # through up to 3 first-launch dialogs (theme picker / auth-method
    # picker / bypass-permissions confirm) before the ready marker
    # appears. The poll loop auto-Enters each dialog at ~1Hz, so a
    # 3-dialog chain plus boot time can run 30-40s; 60s gives headroom.
    from claudeteam.runtime import tunables
    ready_timeout = float(tunables.tunable("wake.ready_marker_timeout_s", 60.0))
    if wake.wait_until_ready(target, adapter, timeout_s=ready_timeout):
        # inject_and_confirm, not a bare inject: a freshly-ready pane can
        # drop the submit key on the fixed-settle paste, leaving the identity
        # prompt sitting unsubmitted until a human Enter. It re-nudges
        # submit until the agent goes busy.
        wake.inject_and_confirm(target, adapter, identity.init_prompt(agent))
        outcome = READY
    else:
        outcome = READY_NO_INIT
    local_facts.upsert_status(agent, "进行中", "initializing")
    return outcome
