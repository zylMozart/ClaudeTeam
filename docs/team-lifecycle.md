# Team Lifecycle: Stop & Destroy

## Stopping the Team (Pause)

Stopping preserves all state (Bitable, group chat, runtime_config.json). Restart anytime.

### Host-native

```bash
tmux kill-session -t <session-name>
```

Restart with:

```bash
bash scripts/start-team.sh
```

### Docker

```bash
docker compose down
```

Restart with:

```bash
docker compose up -d
```

---

## Destroying the Team (Full Reset)

Use `scripts/reset.sh` to reset a deployment back to fresh-clone state.

```bash
scripts/reset.sh              # dry-run: preview what will be cleaned
scripts/reset.sh --yes        # execute: remove runtime state + Feishu Bitable
scripts/reset.sh --yes --nuke # also delete workspace/, agents/*, team.json
```

### What each level cleans

| Level | What it removes |
|-------|-----------------|
| `--yes` | Docker containers, runtime_config.json, PID files, Feishu Bitable |
| `--yes --nuke` | All of the above + workspace/, agents/*/workspace, agents/*/identity.md, team.json |

### Notes

- Default is **dry-run** — nothing is touched without `--yes`
- Requires typing the session name as confirmation to prevent accidents
- Feishu group chat must be dismissed manually (API limitation) — the script prints instructions
- After reset, re-run `scripts/setup.py` + `scripts/start-team.sh` (or `docker compose run --rm team init` + `docker compose up -d`) to start fresh
