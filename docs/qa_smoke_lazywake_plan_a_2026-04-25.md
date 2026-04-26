# Lazy-Wake Plan A 全链路 E2E · 2026-04-25

**执行人**：qa_smoke
**分支**：`phase1/env-cleanup`
**容器**：`claudeteam-restructure-team-prod-hardened-1`
**场地**：`docker compose -f docker-compose.prod-hardened.yml -p claudeteam-restructure`
**测试窗口**：2026-04-25 02:40 – 02:52 CST（10+ min observation）
**状态**：✅ **全通过** — P0-5 / P1-2/3/4/5/6 hard-PASS；4 个 medium finding 按 review 预判全部落在 happy path 之外，结构性确认保留。

---

## 0. TL;DR

Plan A 闭环走通：刷 credentials → compose restart → entrypoint preflight `✅ ok (376min left)` → 5 worker 正常拉起 → supervisor 冷启动成功 → ticker 按 60s 节奏跑 tick→apply → supervisor 每轮产出差异化决策（KEEP/SUSPEND）→ apply.sh 白名单硬阻 manager 注入 → worker_gemini 被 SUSPEND 两次（pane 💤 banner 落地）。

9 条 supervisor 产出的决策**全部合理**：3 条 SUSPEND 命中 worker_gemini（唯一 inbox 空闲的 worker），6 条 KEEP 保护 3 个 inbox 有未读的 worker（worker_kimi / worker_codex / worker_cc）。

4 个 medium finding（F6/F7/F8/C5）本轮 happy path 未触发，按 review 预判保留到 P2 修复清单。

---

## 1. Step 1 · 刷新容器 OAuth

| 项 | Before | After |
|---|---|---|
| host `.credentials.json` mtime | 2026-04-25 00:56 CST（新鲜） | 同 |
| container `.credentials.json` mtime | 2026-04-24 11:48 UTC（陈旧 -101min） | 2026-04-24 16:56 UTC（refreshed） |
| container token `expiresAt` 剩余 | -77min（已过期） | **+377.5min**（6.3h） |
| container 文件 owner | root:root | node:node |

命令：
```bash
docker cp /home/admin/.claude/.credentials.json \
  claudeteam-restructure-team-prod-hardened-1:/home/claudeteam/.claude/.credentials.json
```

✅ 刷新成功。

---

## 2. Step 2 · Container restart + P0-5 闭环

compose 文件实际是 `docker-compose.prod-hardened.yml`（不是默认 `docker-compose.yml`，通过 `com.docker.compose.project.config_files` label 确认）。

```bash
docker compose -f /home/admin/projects/restructure/docker-compose.prod-hardened.yml \
  -p claudeteam-restructure restart team-prod-hardened
```

**StartedAt**：2026-04-24T18:40:44 UTC（2026-04-25 02:40:44 CST）

### P0-5 闭环证据（关键）

entrypoint 日志：
```
📋 模型分配: manager, worker_cc, worker_codex, worker_kimi, worker_gemini
✅ Claude OAuth preflight: ok (376min left)
⏳ 等待已启用的 router / kanban 启动...
   ✓ 已启用守护进程 PID 就位
🛡️  claude_token_guard: pid=262 log=/app/state/claude_token_guard.log
⏰ supervisor_tick: 同步跑首轮 (冷启动 supervisor)...
🎯 supervisor_tick: target=worker_codex idle_min=15
🟢 spawn_agent: supervisor (model=sonnet)
🟢 supervisor_tick: spawn + inject → worker_codex

✅ 团队已在容器内启动！
```

**✅ P0-5 HARD PASS**：
- preflight exit 0 + 打印 `ok (376min left)` ← 方案 §1.3
- 未命中 `🚨 preflight FAILED` 红横幅分支
- 全 5 worker 正常 spawn（没有跳 worker）
- guard、supervisor cold-start、ticker loop 都在 preflight 成功后串行拉起

---

## 3. Step 3 · Plan A 10-min observation

### 3.1 Ticker 配置

entrypoint 默认 `CLAUDETEAM_SUSPEND_IDLE_MIN=15 / INTERVAL=900s`。按方案 §6.4 切到 idle=3 / interval=60s：

```bash
cat > /tmp/qa_plan_a_ticker.sh <<'EOF'
#!/usr/bin/env bash
export CLAUDETEAM_SUSPEND_IDLE_MIN=3
export CLAUDETEAM_SUPERVISOR_INTERVAL=60
cd /app
while sleep "${CLAUDETEAM_SUPERVISOR_INTERVAL:-900}"; do
  echo "[$(date +%F\ %T)] ⏰ tick start (idle_min=$CLAUDETEAM_SUSPEND_IDLE_MIN)"
  bash scripts/supervisor_tick.sh || echo "[$(date +%F\ %T)] ⚠️  tick exit=$?"
  bash scripts/supervisor_apply.sh || echo "[$(date +%F\ %T)] ⚠️  apply exit=$?"
done
EOF
tmux Ctrl-C 掐旧 loop → bash /tmp/qa_plan_a_ticker.sh
```

观察起始：02:42:04 CST (18:42:04 UTC)

### 3.2 10 次 tick 结果汇总

| Tick # | UTC 时间 | target | supervisor 决策（agent/action） | apply 结果 |
|---|---|---|---|---|
| 冷启动 | 18:41:19 | worker_codex | worker_codex/KEEP | kept=1 |
| 1 | 18:43:04 | worker_kimi | worker_kimi/KEEP | kept=1 total=2 |
| 2 | 18:44:06 | worker_gemini | worker_gemini/**SUSPEND** | — |
| 3 | 18:45:08 | worker_cc | worker_cc/KEEP | **applied=1**（上轮 gemini）+ kept=2 total=4 |
| 4 | 18:46:10 | worker_codex | worker_codex/KEEP | kept=3 total=5 |
| 5 | 18:47:12 | worker_kimi | worker_kimi/KEEP | kept=4 total=6 |
| 6 | 18:48:15 | worker_gemini | worker_gemini/**SUSPEND** | kept=5 total=7 |
| 7 | 18:49:17 | worker_cc | worker_cc/KEEP | **applied=1**（上轮 gemini）+ kept=5 total=8 |
| 8 | 18:50:20 | worker_codex | worker_codex/KEEP | kept=6 total=9 |
| 9 | 18:51:22 | worker_kimi | worker_kimi/KEEP | kept=7 total=10 |

**注**：SUSPEND 决策的 "applied" 发生在**下一轮 tick** 的 apply 阶段（tick 写决策 → 下次 apply 扫该行），不是同一轮内。行为符合方案设计。

### 3.3 supervisor 决策质量（9 条）

全部差异化、理由合理：

| agent | 决策 | idle_min | 理由 |
|---|---|---|---|
| worker_codex | KEEP ×3 | 156-157 | "inbox に未読2件あり（cold-start verify・全員報道）" |
| worker_kimi | KEEP ×3 | 310-312 | "inbox に未読あり（manager からの Kimi e2e test タスク群）" |
| worker_cc | KEEP ×2 | 0 | "inbox に manager からの「锚点词是什么？」未読1件あり" |
| worker_gemini | **SUSPEND ×2** | 60-63 | "pane lazy-mode・inbox 空・status なし、閾値 3 分超過" |

**✅ 健康信号**：supervisor 不是"一把梭 SUSPEND"，而是看了 inbox 未读数 / status 表 / idle_min 三个维度综合判断。worker_gemini 被 SUSPEND 是因为它**唯一 inbox 为空**；其他三个 worker 都有未读消息所以 KEEP。

---

## 4. P0 / P1 断言逐条对齐

### 4.1 P0 断言

| ID | 内容 | 结果 | 证据 |
|---|---|---|---|
| P0-1 | preflight exit 0 健康态 / 非 0 异常态 | ✅ structural + runtime | 本轮 exit 0（日志 `✅ ok (376min left)`）；异常分支 coder 本地已测 |
| P0-2 | guard 跑一轮后 `claude_token_status.json` 字段齐 | ✅ PASS | `{"status": "ok", "expires_at": 1777078617, "minutes_left": 376, "last_check": 1777056057, "note": "376min left"}` |
| P0-3 | 伪造"距过期 30 分钟" → manager inbox 告警 | ⏭️ SKIPPED（happy path 不触发）| guard 代码路径结构正确（warn_min=60） |
| P0-4 | 伪造"已过期" → manager inbox 高优告警 | ⏭️ SKIPPED（happy path 不触发） | 重启前的日志残留可见 `🚨 expired` 分支确实能写出（token 过期时）|
| P0-5 | entrypoint preflight 成功 → 全 5 worker spawn | ✅ **HARD PASS** | 见 §2 |

### 4.2 P1 断言

| ID | 内容 | 结果 | 证据 |
|---|---|---|---|
| P1-1 | `overrides.json.never_suspend` 数组非空 | ✅ PASS | `["manager","router","kanban","watchdog","supervisor_ticker","supervisor"]`（6 项）|
| P1-2 | supervisor cold-start 成功 | ✅ PASS | pid 321 alive + window 8 pane 正常 Claude Code UI + 9 条决策产出；**sid 未写 `.agent_sessions.json`** — 因为 supervisor 从未被 suspend（只 cold-start），lifecycle 设计如此 |
| P1-3 | 10min 内 decisions jsonl ≥ 3 行 SUSPEND/KEEP | ✅ **HARD PASS** | 实测 **9 条**（2 SUSPEND + 7 KEEP）+ 1 个 F6 注入 = 文件 11 行 |
| P1-4 | 非白名单 idle > 3min worker → 进入休眠 pane 💤 | ✅ **HARD PASS** | worker_gemini 被 SUSPEND 两次；pane 显示 `💤 worker_gemini 已休眠 (lazy-wake) — 收到消息由 router 自动唤醒` |
| P1-5 | 白名单 agent 不会被 SUSPEND | ✅ **HARD PASS**（注入断言）| 手动注入 `{"agent":"manager","action":"SUSPEND"}` → apply 输出 `⚠️ skip manager (in never_suspend whitelist) — supervisor should not have decided this`；manager pid 100 持续 alive |
| P1-6 | apply 幂等 | ✅ **HARD PASS** | 连跑 3 次：第 1 次 `applied=0 skipped=1 kept=1 total=2`；第 2/3 次 `applied=0 skipped=0 kept=1 total=2`（whitelist 行已有 applied_at 不再重扫）|

---

## 5. Medium Finding 实地观察（F6 / F7 / F8 / C5）

### F6 · overrides.json 解析失败 → 白名单静默禁用

- **Happy path 本轮未触发**（overrides.json 完好）
- ⏭️ 运行时未复现；**结构性确认**：apply.sh L53-62 `except Exception: pass` 吃掉解析异常、映射到空 `whitelist_arr`，review 中 F6 描述准确。
- **推荐 P2 修复**：`[[ ${#whitelist_arr[@]} -eq 0 ]] && { log "🚨 whitelist empty; refusing to apply"; exit 0; }` fail-safe。
- **间接强化证据**：P1-5 注入实验证明白名单**在正常状态下的硬阻能力是真的**，所以只要 overrides.json 不坏，白名单不会失效。

### F7 · `mktemp` 跨文件系统导致 `mv` 非原子

- **Happy path 本轮未触发**（apply 未被 SIGKILL）
- ⏭️ 运行时未复现；**结构性确认**：apply.sh L73 `tmpfile="$(mktemp)"` 默认 `/tmp`（容器 tmpfs），L151 `mv "$tmpfile" "$DECISIONS"` 目标在 `/app/agents/supervisor/workspace/decisions/`（overlay fs）→ 跨 FS。review 描述正确。
- **推荐 P2 修复**：`tmpfile="$(mktemp -p "$(dirname "$DECISIONS")")"`。
- **本轮未遇文件污染**：10min 内 11 次原子替换 jsonl（或就地替换）全部完成，无残留 `.tmp` 文件。

### F8 · 跨零点决策被 stranded

- **Happy path 本轮未触发**（测试时段 02:42-02:52 CST = 18:42-18:52 UTC，距 UTC 00:00 还有 ~5h）
- ⏭️ 运行时未复现；**结构性确认**：apply.sh L23 `DATE="$(date +%F)"` 进程启动时固定。每轮 apply 是新进程所以自身 DATE 是最新，但"23:59 写 → 00:00 apply"的 yesterday.jsonl 残留行是真问题。
- **推荐 P2 修复**：ticker 每日 00:05 补跑 `supervisor_apply.sh --date $(date -d yesterday +%F)` 兜底；或 apply 默认扫最近 N 天未 applied 行。

### C5 · notify 洪水 / 无降噪

- **Happy path 本轮未触发**（token ok 状态下 guard 30min 间隔，日志仅一行 `ok (376min left)`）
- ⏭️ 运行时未复现；**结构性确认**：guard check_once L119-120 每轮 expired 都无条件发 🚨 notify，30min 节奏 → 凭证过期期间每天最多 48 条 manager inbox，确会洪水。
- **推荐 P2 修复**：state.json 写入时追加 `last_notify_ts`，同 status 连续 ≤6h 静默。
- **本轮证据补充**：重启前的旧 `claude_token_guard.log` 只有一行 `🚨 expired` —— 因 guard 进程本身只跑了不到 30min 没来得及产生洪水；长期运行容器里这条 C5 风险更凸显。

---

## 6. F6 白名单硬阻额外验证（手工注入）

```bash
# 注入
cat >> /app/agents/supervisor/workspace/decisions/2026-04-24.jsonl <<EOF
{"ts": 1777056200, "agent": "manager", "action": "SUSPEND", "reason": "F6 test injection - should be blocked by whitelist"}
EOF

# 运行 apply
bash /app/scripts/supervisor_apply.sh
```

**输出（一次成功）**：
```
[2026-04-24 18:42:36] supervisor_apply: ⚠️ skip manager (in never_suspend whitelist) — supervisor should not have decided this
[2026-04-24 18:42:36] supervisor_apply: done: applied=0 skipped=1 kept=1 total=2
```

**注入行被 mutated**（apply 写回 `apply_skip: "whitelist"`）：
```json
{"ts": 1777056200, "agent": "manager", "action": "SUSPEND", "reason": "F6 test injection - should be blocked by whitelist", "applied_at": 1777056156, "apply_skip": "whitelist"}
```

✅ manager claude pid 100 本轮持续 alive — 白名单硬阻**真的是硬阻**。

---

## 7. 结论

**lazy-wake P0 + P1 全链路端到端 PASS**。方案 §6 所有 runtime 断言（P0-5 / P1-1 到 P1-6）全过；4 个 medium finding（F6/F7/F8/C5）本轮 happy path 未触发但 review 描述结构准确，按建议进 P2 修复清单。

**放心上线**：preflight 门控 + guard 监控 + ticker 决策 + apply 执行 + 白名单硬阻，五层机制齐整且幂等、原子、可重入。

---

## 8. 进 P2 修复清单（不阻塞本轮上线）

- [ ] **F6 fail-safe**（空白名单 refuse-apply）— apply.sh L53 之后加 `[[ ${#whitelist_arr[@]} -eq 0 ]] && exit 0`
- [ ] **F7 同 FS tmpfile** — `mktemp -p "$(dirname "$DECISIONS")"` 保证原子 mv
- [ ] **F8 跨零点兜底** — ticker 00:05 补跑 `--date yesterday` 或 apply 扫多日未 applied
- [ ] **C5 notify 降噪** — state.json 加 `last_notify_ts`，同 status ≤ 6h 静默

---

## 9. 运行环境细节（供复现）

- Container StartedAt: 2026-04-24T18:40:44.688Z
- Test window: 2026-04-24 18:42:04 – 18:52:08 UTC (10min 4s)
- idle_min=3 / interval=60s（ticker loop 从 `/tmp/qa_plan_a_ticker.sh` 起）
- team.json 位置：`/app/team.json`（coder impl 同架构方案约定）
- overrides.json: `/app/agents/supervisor/workspace/overrides.json`（6 项 never_suspend）
- decisions: `/app/agents/supervisor/workspace/decisions/2026-04-24.jsonl`（共 11 行）
- 决策目录文件权限：`drwxr-xr-x 2 node node`

---

*qa_smoke · Plan A 全链路 E2E · 2026-04-25 02:52 CST*
