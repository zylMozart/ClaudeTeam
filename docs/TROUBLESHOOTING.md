# Troubleshooting

Last updated: 2026-04-25

## How To Use This Page

1. Find your symptom below.
2. Run the listed quick checks.
3. Apply the minimum corrective action.
4. Capture evidence and report.

## Symptom Matrix

### 1) Group messages arrive but no agent responds

Quick checks:

- router process alive (`pgrep -f feishu_router.py`)
- router cursor heartbeat freshness (`/app/state/router.cursor` mtime; legacy `scripts/.router.cursor` still read for backwards compat but deprecated)
- subscription profile/app mismatch

Likely causes:

- event subscription missing or stale
- router disconnected and not recovering
- wrong profile/chat boundary

First action:

- restart router path only, then verify one real message flow

### 2) Message stuck / delayed delivery

Quick checks:

- inspect `workspace/shared/.pending_msgs/*.json`
- confirm target pane is idle or wakeable
- check manager unread backlog warnings

Likely causes:

- busy pane blocked injection
- queue backlog not draining

First action:

- preserve FIFO queue; avoid manual queue file edits unless incident lead approves

### 3) `send` reports partial success or group notify failure

Quick checks:

- local inbox write happened
- Feishu group send call status

Likely causes:

- remote IM temporary failure while local core remains healthy

First action:

- do not blindly retry if local write already succeeded (avoid duplicates)

### 4) Frequent `800004135` rate-limit errors

Quick checks:

- router catch-up polling behavior
- kanban daemon write frequency
- workspace log fan-out volume

Likely causes:

- bursty Bitable writes or high-frequency polling

First action:

- reduce non-critical remote writes before touching core routing logic

### 5) Slash command returns duplicate responses

Quick checks:

- stale/duplicate router subscription processes
- old container/session still consuming same events

Likely causes:

- multiple active subscribers on same app/profile/chat

First action:

- stop stale subscriber first; keep only one valid router path

### 6) Live smoke blocked with `message_count=0`

Quick checks:

- test group/user profile readiness
- app/profile isolation correctness
- QA approved user availability

Likely causes:

- no real user-text event source in test group

First action:

- keep result red; do not claim pass from rehearsal-only bot traffic

### 7) Claude OAuth 401 (worker_cc spawn 立刻挂)

Symptom:

- `docker logs` 出 `Invalid bearer token` / 401;
- manager 正常但新 spawn 的 worker_cc 立刻死;
- preflight 失败 → entrypoint 只拉 manager + pane 红色横幅。

Quick checks:

- `bash scripts/preflight_claude_auth.sh; echo $?` (0=ok / 2=warn / 3=expired)
- `cat /app/state/claude_token_status.json | jq .status` (ok | warning | expired | api_key_mode)
- 容器内 `pgrep -f claude_token_guard` (guard 进程是否活)
- 容器内 `tail /app/state/claude_token_guard.log`

Likely causes:

- `/home/claudeteam/.claude/.credentials.json` 的 `expiresAt` 已过期 (小时级 TTL)
- refresh_token 从未续期 — OAuth 的 device flow 需浏览器,容器里做不到

First action (host 侧人工):

1. 宿主机跑 `claude /login` 走浏览器 device flow → 文件自动刷新
2. 如果容器凭证不是直接 bind-mount 主机的,用 `docker cp ~/.claude/.credentials.json <container>:/home/claudeteam/.claude/.credentials.json` 覆盖
3. `docker compose restart team` → entrypoint preflight 应转绿
4. 灾备: `.env` 填 `ANTHROPIC_API_KEY=sk-ant-...` + 重建容器 (guard 检测到会短路 exit,不再告警;**成本注意: API key 模式按调用计费**)

### 8) supervisor 没产出 (decisions jsonl 空 / cursor 不动)

Symptom:

- `agents/supervisor/workspace/decisions/$(date +%F).jsonl` 不存在或行数不增长;
- `/app/state/supervisor_cursor.txt` 不变或缺失;
- supervisor pane 反复出 "🌅 wake_agent: supervisor 冷启动".

Quick checks:

- `ls agents/supervisor/workspace/` 结构齐 (`overrides.json` + `decisions/`)
- `jq .never_suspend agents/supervisor/workspace/overrides.json` 非空
- `cat /app/state/supervisor_cursor.txt`
- supervisor pane 是否 401 (复用同一套 Claude OAuth 凭证,见 §7)

Likely causes:

- P0 (§7) 未修 → supervisor 也 401 → 每轮 tick 冷启动后秒死,没写决策
- `agents/supervisor/workspace/` 目录不存在 (新容器漏 docker cp)
- overrides.json 的 `never_suspend` 把所有 agent 都圈进来了 → 没目标可处理

First action:

1. 先修 §7 (P0 是所有问题的上游)
2. 手动跑 `bash scripts/supervisor_tick.sh` 观察输出;decisions jsonl 应追加一行
3. `bash scripts/supervisor_apply.sh` 再跑,应执行该行 SUSPEND 并回填 applied_at
4. 验收 §1 P1-3: idle_min=3 interval=60 跑 10min,decisions 应 ≥ 3 行

### 9) 凭证同步原理 (dev / live-smoke / prod-hardened)

> 背景：本仓 docker-compose 有三套 profile，凭证语义不一样；§7 401 和 §8 supervisor 死活都先看这里判断当前跑的是哪一套。详见 `docs/architect_creds_persistence_v2_2026-04-25.md`。

| profile | service | claude OAuth 来源 | host /login 同步 |
|---|---|---|---|
| dev (`docker-compose.yml`) | `team` | host `~/.claude/.credentials.json` + `~/.claude.json` 单文件 bind-mount，`user: 0:0` | ✅ 秒级 |
| live-smoke (`docker-compose.live-smoke.override.yml`) | `team` | 完全继承 dev（override 自 2026-04-25 v2 起为 `services: {}` 占位） | ✅ 秒级 |
| prod-hardened (`docker-compose.prod-hardened.yml`) | `team-prod-hardened` | runtime root 下独立 creds 副本，不挂 host | ❌（容器目前已 down；复活时按 v1 spec 另议） |

**同步原理**：`claude /login` 在 host 侧写 `.credentials.json` 时走 `tmpfile → rename(2)`，linux mount namespace 对 rename 透明，容器侧的单文件 bind-mount 跟随新 inode。所以 host 一次 `/login` 即等于全部 dev/live-smoke 容器同步。

**排查命令（dev/live-smoke）**：

```bash
# host 侧
ls -la --time=ctime ~/.claude/.credentials.json   # 记 ctime A

# 容器侧
docker exec <container> stat /home/claudeteam/.claude/.credentials.json   # ctime 应 == A

# host /login 后再来一次，A→B 应一致
```

ctime 不同 → bind-mount 链路坏了，回 §1 的 dev / §2 的 override。

**401 / 凭证过期手册**：

1. host 跑 `claude /login` 走浏览器 device flow，文件自动刷
2. 容器侧 `bash scripts/preflight_claude_auth.sh; echo $?` 应转 0
3. 仍红 → `docker compose restart team` （让 entrypoint 再跑一次 preflight + guard）
4. 极端 → `.env` 填 `ANTHROPIC_API_KEY=sk-ant-...` 走 API key 模式（按调用计费）

prod-hardened 复活的话，host /login 不会同步进容器，要么 docker cp 凭证、要么按 v1 spec 上 `lib/creds_sync.sh`。

## Escalation Rule

Escalate to manager when:

- security boundary may be violated,
- owner action or external credential operation is required,
- repeated restart attempts enter cooldown without recovery.
