# Legacy Bitable Degradation Smoke

Superseded for default acceptance by `docs/no_bitable_core_smoke.md`.
This file now describes optional legacy/export behavior only; it is not the
TASK-021 default no-live gate.

This smoke path validates the TASK-020 default boundary: local core commands
must keep working without real Feishu credentials, Bitable, `lark-cli`, tmux,
Docker, or network access.

It supports TASK-020/TASK-021 style container verification where the goal is to
prove local core contracts before live credentials are available.

## Scope

No-live means:

- no `lark-cli`, `npx`, or `npx @larksuite/cli`
- no real Feishu app, Bitable, chat, or tenant
- no tmux injection
- no Docker socket access from the test process
- no network access

The runner installs `tests/no_live_guard.py` before executing tests. A passing
default no-live run is evidence that default verification did not call live
Feishu/Bitable/lark tooling.

## Commands

Host no-live:

```bash
cd /home/admin/projects/restructure
python3 tests/run_no_live.py
```

Wrapper command:

```bash
cd /home/admin/projects/restructure
python3 scripts/run_no_live_tests.py
```

Container no-live, after the image is built:

```bash
cd /home/admin/projects/restructure
export COMPOSE_PROJECT_NAME=claudeteam-restructure
docker compose run --rm --no-deps --entrypoint python3 team tests/run_no_live.py
```

The container command does not require `docker compose run team init` and does
not require Feishu credentials because it overrides the entrypoint and only runs
offline tests.

## Scenarios Covered

`scripts/regression_local_facts.py` covers the P0 local core chain:

1. `send` survives Bitable create failure and keeps the full local inbox body.
2. `inbox` reads the local durable store.
3. `read` marks exactly the requested local message and does not fake read
   state.
4. `status` survives Bitable search/update/create failure and persists local
   status.

`tests/no_live_guard.py` additionally blocks `lark-cli`, `npx`, tmux, Docker,
network, and credential env vars for the default runner.

`tests/bitable_degradation_smoke.py` is legacy/export coverage for optional
kanban behavior only. It covers:

1. Bitable disconnected while fetching agent status.
   Expected behavior: skip the sync round and preserve previous projection.
2. Bitable record list rate-limited while reading kanban rows.
   Expected behavior: do not delete or create rows.
3. Batch delete rate-limited.
   Expected behavior: do not create new rows after a failed delete.
4. Batch create rate-limited.
   Expected behavior: stop the current round and let the next full sync retry.
5. Kanban disabled or missing `kanban_table_id`.
   Expected behavior: fail loudly with an operator-visible message.

## Expected Evidence

Successful host output ends with:

```text
OK: no-live Bitable degradation smoke passed
✅ local facts regression passed
no-live tests: 6/6 passed
```

For container evidence, preserve:

- compose project name
- image id or build log reference
- exact command
- final `no-live tests: 6/6 passed` line

## Live Boundary

Live Feishu smoke is a separate approval step. Do not convert default tests into
real `lark-cli` calls.

When live credentials exist, run this no-live suite first, then run a separate
operator-approved live smoke or legacy export check that records app/profile/chat
or table IDs without printing secrets.

## Failure Classifier For QA

| Probe Result | Meaning | Expected Default CLI Behavior |
|---|---|---|
| Bitable create/search/update/list all raise | Legacy adapter unavailable | `send -> inbox -> read -> status` still exits 0 when local store works. |
| `lark-cli` or `npx` blocked | Live/export unavailable | Default command chain must not call it. |
| local inbox write raises | Core write failed | `send/direct` exits non-zero and must not claim delivered. |
| local read id missing | Core read failed | `read` exits non-zero and must not mark another record. |
| local status write raises | Core status failed | `status` exits non-zero and must not claim current status. |
| tmux notification raises | Delivery notification degraded | Message remains in local inbox; pending queue/log records retry need. |
