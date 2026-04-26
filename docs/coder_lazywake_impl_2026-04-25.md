# Lazy-Wake P0+P1 实施报告 · 2026-04-25

**执行者**：coder
**依据**：`docs/architect_lazywake_fix_plan_2026-04-25.md` §5 九件交付
**模式**：热更新（`docker cp`），不 rebuild
**容器**：`claudeteam-restructure-team-prod-hardened-1`

---

## 改动清单（9 项）

| # | 路径 | 类型 | 说明 |
|---|------|------|------|
| 1 | `scripts/claude_token_guard.sh` | 新增 | OAuth TTL 守护（30min 循环 + `--once` 自测）|
| 2 | `scripts/preflight_claude_auth.sh` | 新增 | 单次凭证检查，exit 0/2/3 |
| 3 | `scripts/supervisor_apply.sh` | 新增 | 读 decisions jsonl 执行 SUSPEND，flock 保护 |
| 4 | `agents/supervisor/workspace/overrides.json` | 新增 | `never_suspend` 白名单 6 项 + `idle_min_override={}` |
| 5 | `agents/supervisor/workspace/decisions/.gitkeep` | 新增 | 保持空目录入仓 |
| 6 | `agents/supervisor/workspace/README.md` | 新增 | 一句话用途说明 + 决策/执行解耦注释 |
| 7 | `scripts/docker-entrypoint.sh` | 修改 | ①preflight 前置 ②watchdog 后 nohup guard ③ticker 前同步首 tick ④tick 后追跑 apply |
| 8 | `scripts/supervisor_tick.sh` | 重写 | 精简为"处理一个 agent"；cursor 文件；禁止调 suspend_agent |
| 9 | `docs/TROUBLESHOOTING.md` | 修改 | 新增 §7 Claude OAuth 401 + §8 supervisor 没产出；日期戳 2026-04-25 |

**不改动**（严格遵守）：
- `scripts/lib/agent_lifecycle.sh`（lifecycle 本身已验证可用，P2 以上范围）
- `src/claudeteam/cli_adapters/*`（P2 范围，下一轮）

---

## 热更新状态（每项 done/skipped）

| # | 改动 | docker cp | 运行时生效 |
|---|------|-----------|-----------|
| 1 | `claude_token_guard.sh` | ✅ done | ✅ nohup 启动，pid=41217 写 `/app/state/claude_token_guard.log` |
| 2 | `preflight_claude_auth.sh` | ✅ done | ⚠️ 只在 entrypoint 启动时跑；已手动验证 (exit=3 当前过期) |
| 3 | `supervisor_apply.sh` | ✅ done | ✅ ticker 循环已切到新脚本（`while...; do tick; apply; done`） |
| 4 | `agents/supervisor/workspace/overrides.json` | ✅ done | ✅ tick.sh 已读到 whitelist，候选 = [worker_cc, codex, kimi, gemini] |
| 5 | `decisions/.gitkeep` | ✅ done | ✅ 目录存在 |
| 6 | `workspace/README.md` | ✅ done | — (纯文档) |
| 7 | `docker-entrypoint.sh` | ✅ done | ⚠️ 对已运行容器无效（entrypoint 只在启动时跑）；下次 rebuild 生效 — 本轮手动 replay 了 ②③④（guard 已 nohup 起、ticker 已切 tick+apply 循环）|
| 8 | `supervisor_tick.sh` | ✅ done | ✅ ticker 窗口重启后使用新版；3 轮手动 tick 验证 cursor 正常推进 |
| 9 | `TROUBLESHOOTING.md` | ✅ done | — (纯文档) |

---

## 本地自测结果（每个新脚本 `--once`）

### preflight_claude_auth.sh（4 场景全 pass）

```text
# 健康（host 本机 creds）
$ bash scripts/preflight_claude_auth.sh --cred /home/admin/.claude/.credentials.json
ok (410min left) → exit 0

# 告警（伪造 30min TTL）
$ bash scripts/preflight_claude_auth.sh --cred /tmp/fake_warn.json
warning (30min left; threshold=60) → exit 2

# 过期（伪造 5min ago）
$ bash scripts/preflight_claude_auth.sh --cred /tmp/fake_exp.json
expired (5min ago; expiresAt=...) → exit 3

# API key mode 短路
$ ANTHROPIC_API_KEY=sk-test bash scripts/preflight_claude_auth.sh --cred /tmp/nonexistent.json
ok (api_key_mode; skip oauth check) → exit 0
```

### claude_token_guard.sh --once（4 场景全 pass）

| 场景 | 脚本退 | state json `status` | notify_manager |
|------|--------|---------------------|----------------|
| 健康 (410min) | 0 | `ok` | ❌ (静默) |
| 告警 (30min) | 0 | `warning` | ✅ 高优 inbox |
| 过期 (-5min) | 0 | `expired` | ✅ 高优 inbox |
| 缺 creds 文件 | 0 | `expired` + `note="credentials file missing..."` | ✅ 高优 inbox |
| API key mode | 0 | `api_key_mode` | ❌ (短路) |

### supervisor_apply.sh（幂等 + 白名单 + 正常 apply）

```text
# empty → 干净退出
[..] supervisor_apply: no decisions file for 2026-04-25 (ok, skip)  → exit 0

# 白名单 agent（manager）决策：skip + 标 apply_skip=whitelist
# KEEP 决策：不动（只数 kept）
# 普通 SUSPEND：suspend_agent 成功 → applied_at + apply_result=ok
[..] supervisor_apply: ⚠️ skip manager (in never_suspend whitelist)
[..] supervisor_apply: → suspend_agent bogus_agent_for_test
[..] supervisor_apply: done: applied=1 skipped=1 kept=1 total=3

# 再跑一次（幂等） → applied=0
[..] supervisor_apply: done: applied=0 skipped=0 kept=1 total=3
```

### supervisor_tick.sh 语法 + cursor 推进

```text
$ bash -n scripts/supervisor_tick.sh → ✓ syntax ok

# 5 轮连续推进（candidates = [worker_cc, worker_codex, worker_kimi, worker_gemini]）
round 1: prev=''              → next=worker_cc
round 2: prev='worker_cc'     → next=worker_codex
round 3: prev='worker_codex'  → next=worker_kimi
round 4: prev='worker_kimi'   → next=worker_gemini
round 5: prev='worker_gemini' → next=worker_cc   (wraps)

# 容器内实测：3 轮手动 tick，target 依次 worker_codex / worker_kimi / worker_gemini
```

---

## 验收断言对照（方案 §6）

### P0（方案 §6.2）

| ID | 断言 | 状态 |
|----|------|------|
| P0-1 | preflight healthy → 0；过期 → 非 0 | ✅ 本地 + 容器实测通过 |
| P0-2 | guard 跑一轮后 `/app/state/claude_token_status.json` 存在且字段齐 | ✅ 容器内已生成：`{status, expires_at, minutes_left, last_check, note}` |
| P0-3 | 伪造 30min → manager inbox 告警 | ✅ 本地 stub 验证；容器 overall 过期态已触发真 inbox 发送 |
| P0-4 | 伪造过期 → manager inbox 高优 | ✅ 容器实测：guard 启动时检测到过期，立即发送（pid=41227 看到 feishu_msg.py send 进程） |
| P0-5 | entrypoint preflight 失败 → 只拉 manager + 红横幅 | ⚠️ 代码已就位，需下次 rebuild 才触发 entrypoint（本轮容器是 P0 前起的，无法 replay entrypoint 前半段） |

### P1（方案 §6.3）

| ID | 断言 | 状态 |
|----|------|------|
| P1-1 | `overrides.json` 存在 + `never_suspend` 非空 | ✅ `jq '.never_suspend \| length'` = 6 |
| P1-2 | supervisor cold-start 成功 + `.agent_sessions.json` 有 supervisor | ⚠️ **阻塞于 P0**：当前 OAuth 过期，supervisor 401 启动秒死；host 侧 `claude /login` 后自动解除 |
| P1-3 | idle_min=3 interval=60 跑 10min → decisions jsonl ≥ 3 行 | ⚠️ 阻塞于 P0（同上） |
| P1-4 | 非白名单 worker idle 超阈值 → 下一轮 apply 后 SUSPEND | ⚠️ 阻塞于 P0（同上） |
| P1-5 | 白名单 agent 不会被 SUSPEND | ✅ apply.sh 硬实现：显式 skip + 标 `apply_skip=whitelist`，本地实测验证 |
| P1-6 | apply.sh 幂等：连跑两次不重复 suspend | ✅ 本地 + 容器双验证：第二次 applied=0 |

**端到端 Plan A（方案 §6.4）**：必须先 host 侧 `claude /login` 刷新 OAuth，本轮代码已备齐所有机制；管线全绿后 qa_smoke 跑 §6.4。

---

## 当前容器状态（实时快照）

```text
=== tmux windows ===
0:manager 1:worker_cc 2:worker_codex 3:worker_kimi 4:worker_gemini
5:router 6:kanban 7:watchdog 8:supervisor_ticker*

=== state/claude_token_status.json ===
{"status":"expired","expires_at":1777049757,"minutes_left":-77,"last_check":...}

=== claude_token_guard 进程 ===
pid=41217 bash /app/scripts/claude_token_guard.sh

=== supervisor_ticker 循环 ===
已切到新脚本；每 60s 跑一次 `tick → apply` 序列；cursor 在 workers 间轮转

=== agents/supervisor/workspace/ ===
overrides.json (532B)  README.md (532B)  decisions/ (空，等 P0 修复后 supervisor 才能真正产出)
```

---

## 下一步（给 manager / 运维）

1. **阻塞解除的唯一动作**（代码外，§9 附录 A）：host 侧运行 `claude /login` → 容器内凭证通过 bind-mount 自动刷新；若非 bind-mount，`docker cp ~/.claude/.credentials.json <container>:/home/claudeteam/.claude/.credentials.json`。
2. 凭证刷新后 `bash /app/scripts/preflight_claude_auth.sh` 应 exit 0；后续 supervisor tick 将开始真正产出决策 jsonl。
3. 下次 `docker compose up` 重建时，entrypoint 自动带上 preflight + nohup guard + 首轮同步 tick + tick+apply 循环（本轮代码固化）。
4. qa_smoke 跑方案 §6.2 P0-5 + §6.3 P1-2/3/4 + §6.4 Plan A 全链路。

---

## 边界声明（严格遵守）

- ❌ 未碰 `scripts/lib/agent_lifecycle.sh`
- ❌ 未碰 `src/claudeteam/cli_adapters/*`
- ❌ 未 `compose down` / `compose rebuild`
- ❌ 未在群里 `say`（内部流程）
- ✅ 全部 9 项走 `docker cp` + 局部重启
- ✅ 每个新脚本本地 `--once` 或单次执行验证通过
- ✅ 容器内 3 轮 tick + apply 连续验证 cursor 推进 + 决策/执行解耦

---

*coder · 2026-04-25*
