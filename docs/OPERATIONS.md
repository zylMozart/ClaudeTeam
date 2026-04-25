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

## Router 死了怎么办（手动应急 · F-ROUTER-1/2）

watchdog 默认每 60s 巡检一次,router cursor 超过 180s 没更新自动 SIGKILL +
在 tmux pane 内复活,正常情况无需人工干预。如果你需要立刻重启
（例如刚 `pkill` 完正在等):

```bash
docker compose exec team bash scripts/router_restart.sh
```

脚本干的事:

1. SIGTERM router pid 文件里的进程 + SIGKILL `lark-cli event +subscribe` /
   `feishu_router.py` 残留
2. 清空 router pane (`Ctrl-C` + `clear`)
3. 走 `scripts/lib/router_launch.sh` 重拼 launch 命令并 `tmux send-keys` 注入

验证 (90s 内 cursor 应当被刷新, 且只有一份订阅进程):

```bash
docker compose exec team ls -l /app/state/router.cursor
docker compose exec team pgrep -af 'lark-cli.*event.*subscribe'
docker compose exec team tmux capture-pane -t "$CLAUDETEAM_TMUX_SESSION:router" -p | tail -10
```
