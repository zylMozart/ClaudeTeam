# Container Hardening Profile

The current Docker deployment is a dev/smoke profile.
It uses root, auto-approve/yolo CLI modes, writable source mounts, and broad
credential mounts to make multi-agent smoke testing practical.

Production needs a separate hardening profile with a smaller trust boundary.

## Profile Split

| Profile | Purpose | Boundary |
|---|---|---|
| `dev-smoke` | Fast rebuilds, CLI login, live Feishu smoke, debugging. | Root is allowed; yolo/full-auto is allowed; source bind mounts may be writable. |
| `prod-hardened` | Long-running deployment after init has succeeded. | Non-root; read-only source; only workspace/state writable; no broad host credential mounts. |

## Prod-Hardened Requirements

1. Run as the image user, not root.
2. Drop Linux capabilities and keep `no-new-privileges:true`.
3. Mount source and static config read-only.
4. Keep writable state in explicit state directories only:
   - `/app/workspace`
   - `/app/state` or a future `CLAUDETEAM_STATE_DIR`
   - CLI credential state directories only when that CLI is enabled
5. Do not mount host `~/.lark-cli`.
   Feishu credentials should come from a deployment secret or `.env` with mode
   `0600`, materialized inside the container.
6. Do not mount host `~/.claude.json` and OAuth files by default.
   Prefer API key or a project-local credential volume with minimum ownership.
7. Treat CLI auto-approve/yolo/full-auto as a dev/smoke setting.
   Production should either disable autonomous shell writes or run agents in a
   restricted workspace with read-only source.
8. Runtime files must not be written under `scripts/`.
   PID files, cursors, queues, and generated runtime config should move to a
   state directory before this profile can be fully enforced.

## Current Blockers

The present entrypoint still writes or expects mutable files under paths that
are normally source-controlled:

- `scripts/runtime_config.json`
- `scripts/.router.pid`
- `scripts/.router.cursor`
- `scripts/.kanban_sync.pid`
- `scripts/.watchdog.pid`
- generated CLI auto-approve files under `$HOME`

Because of that, a strict read-only `/app/scripts` production container is a
target architecture, not a fully compatible switch today.

## Proposed Compose Shape

This is the intended production boundary after state-dir support is in place:

```yaml
services:
  team:
    build: .
    user: "claudeteam"
    read_only: true
    cap_drop:
      - ALL
    security_opt:
      - no-new-privileges:true
    tmpfs:
      - /tmp
      - /home/claudeteam/.cache
      - /home/claudeteam/.claude
      - /home/claudeteam/.local
    environment:
      - HOME=/home/claudeteam
      - CLAUDETEAM_STATE_DIR=/app/state
      - CLAUDETEAM_LAZY_MODE=on
    volumes:
      - ./team.json:/app/team.json:ro
      - ./scripts:/app/scripts:ro
      - ./docs:/app/docs:ro
      - ./config:/app/config:ro
      - ./templates:/app/templates:ro
      - ./agents:/app/agents:ro
      - ./workspace:/app/workspace:rw
      - ./runtime_state:/app/state:rw
```

## Validation Commands

Dev/smoke no-live validation:

```bash
cd /home/admin/projects/restructure
python3 tests/run_no_live.py
docker compose config --quiet
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
for p in [".env", ".codex-credentials", ".kimi-credentials", ".gemini-credentials"]:
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

Do not present a root/yolo/full-auto container as production hardened.
It is acceptable for dev and smoke only.

Production acceptance requires a follow-up code change that moves runtime state
out of `scripts/` and lets the entrypoint consume `CLAUDETEAM_STATE_DIR`.
