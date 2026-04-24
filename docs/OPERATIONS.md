# Operations

Last updated: 2026-04-23

## Purpose

This document is the operator handbook for running and maintaining ClaudeTeam safely.

## Runtime Modes

### Host-native mode

```bash
python3 scripts/setup.py
bash scripts/start-team.sh
```

### Docker mode

```bash
docker compose build
docker compose run --rm team init
docker compose up -d
```

## Daily Operator Checklist

1. Confirm runtime files exist: `team.json`, `scripts/runtime_config.json`.
2. Confirm tmux session is alive: `tmux ls`.
3. Confirm key daemons are healthy:
- router
- watchdog
- (optional) kanban daemon
4. Confirm manager can read inbox and respond.

## Core Commands

### Messaging and state

```bash
python3 scripts/feishu_msg.py inbox manager
python3 scripts/feishu_msg.py status manager 进行中 "<task>"
python3 scripts/feishu_msg.py send <agent> manager "<message>" 高
python3 scripts/feishu_msg.py say manager "<reply>"
```

### tmux and process checks

```bash
tmux ls
tmux attach -t <session>
pgrep -f feishu_router.py
pgrep -f watchdog.py
```

### No-live verification

```bash
python3 tests/run_no_live.py
```

## Incident Handling (First 10 Minutes)

1. Capture evidence first:
- current error text
- daemon process list
- pending queue file state
- router cursor freshness
2. Classify incident:
- message routing issue
- queue backlog issue
- Feishu/Bitable rate limit
- credential/profile isolation issue
3. Apply minimum-change fix:
- avoid broad reset if local core still works
- prefer targeted restart of affected daemon only
4. Report with evidence paths and next action.

## Safe Restart Strategy

1. Restart only the failed surface first.
2. Re-check manager inbox and one end-to-end send/receive path.
3. If duplicate routing is observed, check stale subscription processes before broad restart.

## Live Smoke Boundary

Live smoke requires explicit credentials and isolated app/profile/group boundaries.
Do not claim pass without user-message -> manager reply -> worker response evidence.

Primary references:

- [live_container_smoke](live_container_smoke.md)
- [hardening_profile](hardening_profile.md)
- [no_bitable_core_smoke](no_bitable_core_smoke.md)

## Change Management Rule

- Keep runtime behavior changes small and reversible.
- Record checkpoint evidence for each maintenance wave.
- Do not couple emergency fixes with broad refactors.
