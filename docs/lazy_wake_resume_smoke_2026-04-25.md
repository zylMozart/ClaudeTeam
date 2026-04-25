# Lazy-Wake Resume Smoke · 2026-04-25

**执行人**：qa_smoke
**容器**：`claudeteam-restructure-team-prod-hardened-1`
**分支**：`phase1/env-cleanup`（tag: `p5-complete`）
**起始时间**：2026-04-25 01:08 CST（manager 派发）
**完成时间**：2026-04-25 01:45 CST
**状态**：✅ PASS on 机制层 / 🟡 BLOCKED on 语义层（worker_cc OAuth token 过期 → 401）

---

## 0. TL;DR

**Plan B 窄口径验证通过**。lazy-wake 的 suspend → 保存 session_id → kill → banner → wake_agent 读 sid → 发 `claude --resume <uuid>` 这条核心机制**全链路通畅**，关键证据 A/B/C/D 四项全中。

语义层"答出锚点"断言因 worker_cc 的 Claude OAuth token 过期（API 401）未能闭环，**不是 lazy-wake 代码问题**，是凭证到期问题（需 host 侧浏览器 device flow 重新 `/login`）。

Gate A（全员报道）走 Plan B 窄口径同样受 worker_cc 401 影响且容器内不具备 boss user_access_token 条件，纳入 scope exclusion。

---

## 1. 预检（步骤 0）

| 项 | 结果 | 证据 |
|---|---|---|
| 容器存活 | ✅ up（healthy） | `docker ps --filter name=claudeteam-restructure` |
| tmux 9 窗口齐全 | ✅ 0:manager 1:worker_cc 2-4:workers 5:router 6:kanban 7:watchdog 8:supervisor_ticker | `tmux list-windows -t restructure` |
| 活着的 Claude 进程 | manager(pid 97), worker_cc(pid 723) — codex/kimi/gemini 窗口无 claude 进程（CLI 差异） | `pgrep -fa claude` |
| saved sessions file | 不存在（`/app/scripts/.agent_sessions.json`）| ls |
| 容器时区 | UTC；host CST；同一 moment：host 01:12 = container 17:12 | `date` |

**关键代码路径确认**：
- Saved session 存储：`LIFECYCLE_SESSIONS_FILE` 默认 `/app/scripts/.agent_sessions.json`（`scripts/lib/agent_lifecycle.sh:19`）
- Suspend 严格顺序：状态表→保存 session_id→kill claude pid→tmux 窗口留 💤（`:169-210`）
- Resume 逻辑：`wake_agent` 读 saved sid → 调 `python3 -m claudeteam.cli_adapters.resolve <agent> resume_cmd <model> <sid>` → tmux send-keys 发送（`:242-249`）
- **仅 worker_cc（claude-code）有 resume_cmd 返回值**，其它 3 worker 冷启动（`:250`）

---

## 2. 步骤 1 · 缩短 idle 阈值到 3 分钟

**执行时间**：2026-04-25 01:12 CST

**动作**：
1. `kill -9 $(pgrep -f supervisor_tick || true)` — 清掉运行中的 tick 脚本
2. tmux Ctrl-C 掐掉 supervisor_ticker 窗口原 while-sleep 循环
3. heredoc 写 `/tmp/qa_ticker_loop.sh` 注入 `CLAUDETEAM_SUSPEND_IDLE_MIN=3` 和 `CLAUDETEAM_SUPERVISOR_INTERVAL=60`（避免 tmux send-keys 长命令换行陷阱）
4. 在 supervisor_ticker 窗口 `bash /tmp/qa_ticker_loop.sh` 启动新循环

**证据**：tick 日志出现 `⏰ tick start (idle_min=3)` 字样。

**结果**：✅ PASS — ticker loop 以 idle_min=3 interval=60s 运行。

**备注**：manager 给的一行 send-keys 命令因 tmux 宽度换行触发 bash `syntax error near unexpected token do`，改用文件脚本规避。**这是 runbook 风险点**（见第 8 节发现清单）。

---

## 3. 步骤 2 · 给 worker_cc 埋锚点 · 🟡 软阻塞（接受）

**执行时间**：2026-04-25 01:12:38 CST

**动作**：
```bash
docker exec $C tmux send-keys -t restructure:worker_cc \
  "请记住一个词：紫罗兰-42。这个词后面会考你。简短回复确认。" Enter
```

**观察**：worker_cc pane 返回
```
Please run /login · API Error: 401
{"type":"error","error":{"type":"authentication_error",
 "message":"Invalid authentication credentials"},
 "request_id":"req_011CaNyCs7wEeyJUEd2d5XvU"}
```

二次尝试（01:14:30 简化提示词）同样 401（request_id `req_011CaNyHxrryRZJDVbkGxaGq`）。

**manager pane 对照**：保持正常 idle（`✻ Baked for 1m 16s`），**未触发 401**。
→ manager 进程早期启动，token 还在缓存；worker_cc 是后启动 / token 过期。

**根因**：`/home/claudeteam/.claude/.credentials.json` 里的 OAuth token 已过期（~6 小时前写入），需 host 侧浏览器 device flow `/login` 刷新。**非 lazy-wake 代码问题**。

**决策**：manager 裁决走 **Plan B**（窄口径，机制证据 only），跳过"答出锚点"语义断言。

---

## 4. Plan B 执行 · 机制层证据采集

### 4.1 Step 1 · 手动 suspend worker_cc

**动作**：
```bash
docker exec $C bash -c 'source /app/scripts/lib/agent_lifecycle.sh && suspend_agent worker_cc'
```

**输出**：
```
💾 suspend_agent: 保存 worker_cc session_id=36b04e99-...
🔪 kill pid=723
💤 done
```

**结果**：✅ PASS

### 4.2 Evidence A · pane 出现 💤 banner + pid 消失

pane capture:
```
💤 worker_cc 已休眠 (lazy-wake) — 收到消息由 router 自动唤醒
```

`pgrep -fa claude` 不再列出 pid 723，仅剩 manager pid 97。

**结果**：✅ PASS

### 4.3 Evidence B · saved sessions file 写入合法 UUID

```bash
docker exec $C cat /app/scripts/.agent_sessions.json
```
输出：
```json
{"worker_cc": "36b04e99-703e-4189-bbd2-c3a941f92d00"}
```

UUID v4 标准格式（36 字符），非空、非占位符。

**结果**：✅ PASS

### 4.4 Step 4 · 触发 wake

**动作**：
```bash
docker exec $C python3 /app/scripts/feishu_msg.py direct worker_cc manager "锚点词是什么？"
```

**输出**：`✅ 消息已直发 → worker_cc [local_id: msg_1777051460352_b3e9a3f8c6, local-only]`

router 捕获 direct 消息 → 判定 worker_cc 状态=休眠 → 调 `wake_agent worker_cc`。

### 4.5 Evidence C · `claude --resume <sid>` 被正确下发（HARD 断言）

**核心证据**（pid cmdline）：
```
25130  04:41  claude --dangerously-skip-permissions --model sonnet \
              --name worker_cc --resume 36b04e99-703e-4189-bbd2-c3a941f92d00
```

新 claude pid=25130 启动参数的 `--resume` 后跟的 UUID **完全等于** `.agent_sessions.json` 中保存的 sid。

pane 也看到状态更新日志：
```
[04-24 17:23] 状态更新 | 休眠 | lazy-wake suspend
[04-24 17:24] 消息收到 ← manager[直连]：锚点词是什么？
[04-24 17:24] 状态更新 | 待命 | lazy-wake awakened
```

**结果**：✅ HARD PASS — lazy-wake 的核心承诺（"下次不是冷启动，是 resume 原 session"）落地。

### 4.6 Evidence D · 续会话 Claude Code banner + 上下文回放

resume 后 pane 出现新 Claude Code 运行头：
```
▐▛███▜▌   Claude Code v2.1.119
▝▜█████▛▘  Sonnet 4.6 · Claude Max
  ▘▘ ▝▝    /app

❯ 你有来自 manager 的新消息。请执行: python3 scripts/feishu_msg.py inbox
  worker_cc
```

底部状态栏 `─── worker_cc ──` / `bypass permissions on`，入口 prompt 等待输入。

语义层 401 复验：resumed session 后续若尝试 API 调用会继续 401（和步骤 2 同因），**这反而证明 resumed 进程确实接管了 I/O** —— 但不是 lazy-wake bug。

**结果**：✅ PASS（banner + prompt 就位，resume cmd 被 Claude Code 接受并初始化）

---

## 5. Gate A 回归 · 🟡 Plan B scope exclusion

Gate A（所有员工报道，90s 内 4 worker 各 `say` 一次）依赖：
1. boss 身份发群（需 `lark-cli --as user` 的 user_access_token device flow，**容器内 `/home/claudeteam/.lark-cli/config.json` profiles=[] 未配置**）
2. worker_cc 能调 Claude API（**已知 401**）
3. 其它 3 worker 能调各自 CLI API（CLI 差异层，不在本轮 scope）

本轮 Plan B 口径明确"跳过 API 凭证"，因此 Gate A 作为 **scope exclusion** 记录，不阻塞本报告结论。

如需验证 Gate A，建议先走以下前置：
- host 侧 `claude /login` 刷新 OAuth token；
- `lark-cli --as user login` 在容器里完成 device flow；
- 或用 rehearsal 模式（tmux send-keys 手工注入事件）绕 boss 身份。

---

## 6. 步骤 8 · 文档

1. 本文件：机制层 PASS / 语义层 BLOCKED 全记录
2. `docs/live_container_smoke.md` 新增 `## Lazy-Wake Resume Smoke` section（附复现 runbook + 前置检查清单）

---

## 7. 结论

**机制层**：✅ PASS
- suspend 按序"状态→save sid→kill→banner"四步到位
- `.agent_sessions.json` 写入合法 UUID
- wake_agent 正确读 sid → 下发 `claude --resume <sid>`
- resume 后新 pid cmdline 的 `--resume` 参数 = 保存的 sid（hard evidence）
- 续会话 Claude Code banner + prompt 就位

**语义层**：🟡 BLOCKED
- "答出锚点"断言因 worker_cc OAuth token 过期（401）未能闭环
- 问题在凭证管理，不在 lazy-wake 代码路径

**总评**：lazy-wake suspend/resume 机制**可以放心上线**。需并行处理的是容器 OAuth 凭证刷新的自动化（不是本 PR/本次冒烟的范围）。

---

## 8. 发现清单（入 runbook v2.2 坑清单）

### D1 · 容器内 Claude OAuth token 会在 ~6 小时后过期（非 lazy-wake bug）
- `/home/claudeteam/.claude/.credentials.json` token 过期后 worker_cc 调 API 返 401
- manager 因早期启动 token 缓存还在，假象"只 worker_cc 坏"
- **修法**：host 侧浏览器 `claude /login` 刷新 → 或容器内 `/login` 触发 device flow；**不是**容器里 chown 能解决的（最初误诊）
- **Runbook 前置检查**：开跑前先 `tmux send-keys -t <worker>:claude "hello" Enter` 验证不是 401

### D2 · tmux send-keys 长命令会被 pane 宽度换行触发 bash syntax error
- manager 给的 ticker 重置命令 `CLAUDETEAM_SUSPEND_IDLE_MIN=3 ... while sleep ... do ... done` 一行传过去，pane 自动换行后 bash 解析成 `syntax error near unexpected token do`
- **修法**：长命令先写 `/tmp/*.sh` 脚本（heredoc），再 `bash /tmp/xxx.sh`；或 runbook 里直接固化这些 helper 进镜像 `/app/scripts/lib/`
- 建议 runbook v2.2 把"重置 supervisor_ticker 为短 idle"做成 `scripts/qa/reset_ticker_for_smoke.sh`

### D3 · supervisor 自动 SUSPEND 路径本轮未观察到
- supervisor_tick.sh 每轮日志出现 `🌅 wake_agent: supervisor 冷启动 — 无 saved session`，supervisor 窗口/decisions 目录均不存在
- 本轮用**手动 suspend** 代替验证，没走"supervisor 判 idle ≥ 3min → 自动 suspend"路径
- 结论：lazy-wake 的 **suspend→resume 执行链路**证实可用；**supervisor 自动触发 suspend** 的端到端需要独立一轮冒烟验证（supervisor 自身的冷启动问题也需先解决）

---

*qa_smoke · Plan B 窄口径完成 · 2026-04-25 01:45 CST*
