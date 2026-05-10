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

Also home for `pane_env_prefix()` — the shell env-var prefix prepended
to every spawn_cmd so worker agents inherit `CLAUDETEAM_STATE_DIR` and
the Feishu env into their `claudeteam say` shell-outs.
"""
from __future__ import annotations

import shlex
from pathlib import Path

from claudeteam.agents import get_adapter, identity
from claudeteam.agents.codex_cli import ensure_workdir_trusted
from claudeteam.runtime import config, paths, tmux, wake
from claudeteam.store import local_facts
from claudeteam.util import env_str


# env vars to propagate from the operator's shell into every spawned pane
# so worker agents' shell-out calls (via Bash tool) see the deployment's
# state dir instead of falling back to ~/.claudeteam.
#
# FEISHU_APP_*/LARKSUITE_CLI_APP_* added 2026-05-08 (bringup B5): when
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
    "FEISHU_APP_ID",
    "FEISHU_APP_SECRET",
    "LARKSUITE_CLI_APP_ID",
    "LARKSUITE_CLI_APP_SECRET",
)


def _path_readable(p: Path) -> bool:
    """Returns True iff `p` can be stat'd. False on PermissionError /
    not-found / any OSError. deploy-issues 2026-05-08 #1: on Linux host
    where /root is mode 700, Path("/root/...").exists() raised
    PermissionError instead of returning False (Python <3.13 behavior),
    killing `claudeteam up` for non-root deployers. Three /root probes
    in this module need the soft semantic."""
    try:
        return p.exists()
    except OSError:
        return False


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
    from claudeteam.agents.claude_code import agent_home as _agent_home
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
    # can hand the pane a `refreshToken` the server has already revoked.
    # 2026-05-07 caught: pane symlinked to stale host file, refresh
    # round-tripped 401, claude blanked the field, pane logged "401
    # Invalid auth credentials". Re-extract on every provision and write
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
    if not keychain_extracted:
        user_creds = Path.home() / ".claude" / ".credentials.json"
        if user_creds.exists():
            try:
                # Copy, not symlink: claude's atomic-write replaces the
                # symlink with a plain file anyway, so start with one.
                #
                # ALWAYS overwrite — pre-2026-05-10 we skipped if cred_link
                # already existed, but that meant a per-agent file from a
                # previous spawn (now hours-old + access_token expired)
                # stuck around forever. claude pane spawned with an old
                # token, hit '401 Invalid auth credentials', stuck.
                # Mirrors the macOS keychain branch above which
                # unlink+write every provision. Cost: ~500-byte file copy
                # per provision, negligible.
                cred_link.write_bytes(user_creds.read_bytes())
            except OSError:
                pass
    user_claude_json = Path.home() / ".claude.json"
    claude_json = home / ".claude.json"
    if _path_readable(user_claude_json) and not claude_json.exists():
        try:
            claude_json.write_bytes(user_claude_json.read_bytes())
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
    # Seed ~/.claude.json from host's read-only mount once. Without
    # `userID` + `oauthAccount` keys claude pops the OAuth login
    # dialog (the credentials.json alone isn't enough — claude checks
    # ~/.claude.json for "you've completed login" state). After the
    # initial copy, the per-agent file is writable so claude can
    # update its own session counters without affecting other agents.
    claude_json = home / ".claude.json"
    host_claude_json = Path("/root/host-claude.json")
    if _path_readable(host_claude_json) and not claude_json.exists():
        try:
            claude_json.write_bytes(host_claude_json.read_bytes())
        except OSError:
            pass


def pane_env_prefix() -> str:
    """Build a shell env prefix that, prepended to a spawn_cmd, makes the
    spawned process inherit CLAUDETEAM_STATE_DIR and the Feishu env so
    worker agents calling `claudeteam say` write to the project state
    dir, not `~/.claudeteam`.
    """
    parts = [f"CLAUDETEAM_STATE_DIR={shlex.quote(str(paths.state_dir()))}"]
    for var in _PROPAGATED_ENV:
        val = env_str(var)
        if val:
            parts.append(f"{var}={shlex.quote(val)}")
    return " ".join(parts)


# Outcome strings returned by provision_pane. Callers print/log differently
# (start uses loop-style "  → spawned", hire uses "✅ hired") so the helper
# stays I/O-free and lets the caller render.
LAZY = "lazy"
READY = "ready"
READY_NO_INIT = "ready_no_init"
SPAWN_FAILED = "spawn_failed"
CONFIG_ERROR = "config_error"


def provision_pane(agent: str, target: tmux.Target) -> str:
    """Provision a freshly-created pane for `agent`.

    Pre-conditions: tmux window for `target` already exists and is empty
    (a shell prompt). Caller is responsible for window creation.

    Steps:
      1. Render + persist agent's identity.md (`agents/<name>/identity.md`).
      2. If agent is `lazy` in team.json: set status 待命, return LAZY.
      3. For codex CLI: ensure cwd is trusted in ~/.codex/config.toml.
      4. Spawn the adapter's CLI in the pane (with pane_env_prefix).
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
        print(f"  ⚠️ {agent}: agent {agent!r} not in team.json", file=sys.stderr)
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
    if cfg.get("lazy"):
        local_facts.upsert_status(agent, "待命", "lazy: CLI starts on first message")
        return LAZY
    if cli == "codex-cli":
        ensure_workdir_trusted(Path.cwd())
    if cli == "claude-code":
        _ensure_claude_agent_home(agent)
    try:
        adapter = get_adapter(cli)
    except KeyError as e:
        # Bad `cli` value in team.json — typo, dropped adapter, etc. One
        # bad agent shouldn't kill `claudeteam start` for the rest of
        # the team. Caller logs + skips.
        import sys
        print(f"  ⚠️ {agent}: {e}", file=sys.stderr)
        return CONFIG_ERROR
    cmd = f"{pane_env_prefix()} {adapter.spawn_cmd(agent, model)}"
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
        tmux.inject(target, identity.init_prompt(agent),
                    submit_keys=adapter.submit_keys())
        outcome = READY
    else:
        outcome = READY_NO_INIT
    local_facts.upsert_status(agent, "进行中", "initializing")
    return outcome
