# Container Runtime Profiles

The current owner guidance prioritizes container-internal manager availability:
the manager must be able to start CLIs, dispatch agents, run tests, and repair
the isolated container environment. Host and credential boundaries still matter,
but `read_only=true` and non-root are no longer acceptance requirements for the
TASK-032 live smoke path.

## Profile Split

| Profile | Purpose | Boundary |
|---|---|---|
| `dev-smoke` | Fast rebuilds, CLI login, live Feishu smoke, debugging. | Root is allowed; yolo/full-auto is allowed; source bind mounts may be writable. |
| `prod-hardened` | Current live-smoke / production-like validation profile. | Root inside the isolated container; writable rootfs/HOME/cache/state/workspace; no host HOME/socket/privileged/host-network; project-local credentials only. |

## Current Boundary Requirements

1. Run with full control inside the container when needed for manager
   availability and CLI self-repair.
2. Keep `no-new-privileges:true`, do not use `privileged`, do not use
   `network_mode: host`, and do not mount `/var/run/docker.sock`.
3. Do not mount host `~/.claude`, `~/.codex`, `~/.lark-cli`,
   `~/.local/share/lark-cli`, or host HOME.
4. Keep writable data in explicit isolated directories:
   - `/app/workspace`
   - `/app/state` or a future `CLAUDETEAM_STATE_DIR`
   - `/home/claudeteam/.cache` and `/home/claudeteam/.npm`
   - project-local credential dirs under `/home/admin/projects/restructure`
5. Live smoke enables Feishu remote only:
   `CLAUDETEAM_ENABLE_FEISHU_REMOTE=1` and
   `CLAUDETEAM_ENABLE_BITABLE_LEGACY=0`.
6. Runtime must not repair missing Codex by running `npm install -g` or `npx`.
   The image build must install and verify `@openai/codex` plus its native
   platform package, and the runtime launcher must fail before Codex reaches
   its own install-remediation path.

## Current Blockers

The default `docker-compose.yml` remains a dev/smoke profile.
`docker-compose.prod-hardened.yml` is now the isolated full-control profile used
for TASK-032 validation after the owner clarified that manager availability has
priority over read-only rootfs.

The router/watchdog runtime files have been moved to state:

- `/app/state/router.pid`
- `/app/state/router.cursor`
- `/app/state/watchdog.pid`
- `/app/state/kanban_sync.pid` when explicit legacy kanban is enabled
- `/app/state/tmux_intercept.log`
- `/app/state/router_messages/`

Legacy `scripts/.router.pid` and `scripts/.router.cursor` are read only for
compatibility if present; default writes go to `CLAUDETEAM_STATE_DIR`.
Entrypoint/watchdog gate live router startup behind
`CLAUDETEAM_ENABLE_FEISHU_REMOTE=1` and legacy kanban startup behind
`CLAUDETEAM_ENABLE_BITABLE_LEGACY=1`.

Codex launch is guarded by `scripts/lib/run_codex_cli.sh`. In the container,
`CLAUDETEAM_CODEX_REQUIRE_NPM_PACKAGE=1` requires the build-time npm package and
native optional dependency before invoking `codex`.

## Proposed Compose Shape

The concrete profile is `docker-compose.prod-hardened.yml`:

```bash
cd /home/admin/projects/restructure
mkdir -p state workspace agents .lark-cli-credentials .claude-credentials \
  .codex-credentials .kimi-credentials .gemini-credentials .qwen-credentials
touch scripts/runtime_config.json
chmod 700 .lark-cli-credentials .claude-credentials .codex-credentials \
  .kimi-credentials .gemini-credentials .qwen-credentials
chmod 640 team.json scripts/runtime_config.json
chmod 770 state workspace agents

COMPOSE_PROJECT_NAME=claudeteam-restructure-live \
CLAUDETEAM_ENABLE_FEISHU_REMOTE=1 \
CLAUDETEAM_ENABLE_BITABLE_LEGACY=0 \
  docker compose -f docker-compose.prod-hardened.yml \
  --profile prod-hardened config --quiet

COMPOSE_PROJECT_NAME=claudeteam-restructure-live \
CLAUDETEAM_ENABLE_FEISHU_REMOTE=1 \
CLAUDETEAM_ENABLE_BITABLE_LEGACY=0 \
  docker compose -f docker-compose.prod-hardened.yml \
  --profile prod-hardened up -d --build team-prod-hardened
```

This profile does not mount host `~/.claude`, `~/.claude.json`, `~/.lark-cli`,
or `~/.local/share/lark-cli`. Project-local credential directories are the only
writable credential mounts.

For lark-cli keychain profiles, mount project-local
`.lark-cli-credentials/local-share` read-only to
`/home/claudeteam/.local/share/lark-cli`. For Claude OAuth, mount project-local
`.claude-credentials/.claude.json` to `/home/claudeteam/.claude.json`.

## Validation Commands

Dev/smoke no-live validation:

```bash
cd /home/admin/projects/restructure
python3 tests/run_no_live.py
docker compose config --quiet
docker compose -f docker-compose.prod-hardened.yml --profile prod-hardened config --quiet
docker inspect claudeteam-restructure-live-team-prod-hardened-1 \
  --format 'user={{.Config.User}} readonly={{.HostConfig.ReadonlyRootfs}} privileged={{.HostConfig.Privileged}} network={{.HostConfig.NetworkMode}} health={{.State.Health.Status}}'
docker exec claudeteam-restructure-live-team-prod-hardened-1 \
  sh -lc 'ps -eo pid,args | grep -E "[c]odex|[f]eishu_router.py|[w]atchdog.py"; ps -eo pid,args | grep -E "[k]anban_sync" || true; ls -la /app/state; touch /app/.write_test /app/scripts/.write_test /home/claudeteam/.cache/write_test /app/state/write_test /app/workspace/write_test && rm -f /app/.write_test /app/scripts/.write_test /home/claudeteam/.cache/write_test /app/state/write_test /app/workspace/write_test'
```

Container no-live validation:

```bash
cd /home/admin/projects/restructure
export COMPOSE_PROJECT_NAME=claudeteam-restructure
docker compose run --rm --no-deps --entrypoint python3 team tests/run_no_live.py
```

Credential boundary check without printing secrets:

```bash
cd /home/admin/projects/restructure
python3 - <<'PY'
from pathlib import Path
for p in [".env", ".lark-cli-credentials", ".claude-credentials",
          ".codex-credentials", ".kimi-credentials", ".gemini-credentials",
          ".qwen-credentials", ".live-smoke-credentials", "state"]:
    path = Path(p)
    if path.exists():
        print(f"{p}: mode={oct(path.stat().st_mode & 0o777)}")
for key in ["FEISHU_APP_ID", "FEISHU_APP_SECRET", "ANTHROPIC_API_KEY"]:
    value = ""
    for line in Path(".env").read_text(errors="ignore").splitlines():
        if line.startswith(key + "="):
            value = line.split("=", 1)[1].strip()
    print(f"{key}: {'set' if value else 'empty'}")
PY
```

## Operator Rule

Do not describe this profile as host-hardened. It is an isolated, full-control
container profile: powerful inside the container, bounded at the host and
credential mount layer.

Acceptance requires manager Codex alive, router/watchdog alive, no `kanban_sync`
by default, no runtime npm/npx repair prompt, no secret leakage, and QA evidence
for real boss/test-user live smoke plus tmux raw cleanliness.
