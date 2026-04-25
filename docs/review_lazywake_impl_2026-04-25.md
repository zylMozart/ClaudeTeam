# Lazy-Wake P0+P1 实施 Code Review · 2026-04-25

**Reviewer**：toolsmith
**输入**：
- `docs/architect_lazywake_fix_plan_2026-04-25.md`（architect 方案）
- `docs/coder_lazywake_impl_2026-04-25.md`（coder 实施报告）
- `docs/lazy_wake_resume_smoke_2026-04-25.md`（昨轮 qa_smoke）

**范围**：`scripts/` + `agents/supervisor/` + `docs/TROUBLESHOOTING.md` + `docker-entrypoint.sh`
**不审**：`scripts/lib/agent_lifecycle.sh`、`src/claudeteam/cli_adapters/*`（P2/P3 范围）
**审查优先级**：安全/正确性 > 架构 > 边界 > 观测 > 风格

---

## 0. 整体结论

### ✅ PASS-with-minor — 不阻塞 qa_smoke Plan A

核心机制（preflight → guard → 决策写入 → apply 执行 → cursor 推进 → 白名单硬阻）设计与实现**一致且可用**；9 件交付全部到位，bash 语法检查全通过（5/5）；决策/执行解耦硬约束落地；方案 §5 checklist 全部命中。

存在 **10 个 finding**（0 blocker / 4 medium / 6 minor），均为**工程硬度**与**长尾稳健性**问题，不影响 qa_smoke 当前验收。Medium 项建议在 P2 批次内修掉；其余 acceptable for v1。

**可以放 qa_smoke 跑方案 §6.2 P0-5 + §6.3 P1-2/3/4 + §6.4 Plan A。**

---

## 1. 按文件 Findings

### 1.1 `scripts/claude_token_guard.sh`

| # | 等级 | 定位 | 说明 |
|---|---|---|---|
| F1 | **medium** | notify 去重缺失 | `check_once` 每轮 expired 都发 🚨，30min 节奏意味着凭证过期期间每日 48 条 manager inbox 洪水。建议写入 `$STATE_FILE` 时追加 `last_notify_ts`，同 status 连续两轮间隔 <N 小时则静默。|
| F2 | minor | shell 插值到 Python `-c` | L119-120 `json.load(open('$STATE_FILE'))` 把变量字面量拼进 Python 源。当前 `$STATE_FILE` 由 `$CLAUDETEAM_STATE_DIR` 组合而来，无用户输入 → 可利用面为 0；但 pattern 与同文件 L50/L65/L81 的 heredoc+argv 模式不一致，未来被复制时容易踩坑。建议统一改 `python3 -c "import json,sys; ..." "$STATE_FILE"`。|
| F3 | minor | 无日志轮转 | `$GUARD_LOG` 由 entrypoint `>>` 追加；长运行容器会无限膨胀。可接受（每 30min 最多几十字节），列入 v2 backlog。|
| F4 | minor | `pid_$!` 无健康校验 | entrypoint L593 打印 pid，但不 `sleep 2 && kill -0 $pid` 验活。若 nohup 启动就因语法问题死掉，guard 形同虚设。建议加一次存活探测。|

**不是 finding**：
- `set -u` 无 `set -e`：在 while-loop 里是有意为之（单轮错误不能让守护退出）。
- API-key 短路、heredoc+argv 的状态写入、三种状态分支（ok/warning/expired）分支完整。

---

### 1.2 `scripts/preflight_claude_auth.sh`

| # | 等级 | 定位 | 说明 |
|---|---|---|---|
| F5 | minor | `--help` 文本截断 | L23 `sed -n '2,12p' "$0"` 取到 L12 止；但 L13-14 `# entrypoint 在拉工作窗口前调用；fail 则不拉 worker + 挂红横幅 (见 §1.3 P0-5)` 属于关键语义注释。建议改成 `'2,14p'` 或改用独立 usage 函数。|

**不是 finding**：
- argv heredoc 模式正确，无 shell 注入面。
- 退出码 0/2/3 与方案约定一致；四场景本地自测通过（coder 报告已证）。
- 毫秒/秒自动探测 (`exp > 2e10`)、file-missing、parse-fail、no-expiresAt 场景分支齐全。

---

### 1.3 `scripts/supervisor_apply.sh`

| # | 等级 | 定位 | 说明 |
|---|---|---|---|
| F6 | **medium** | overrides.json 解析失败 ⇒ 白名单静默禁用 | L53-62 `mapfile < <(python3 -c "...")`；若 overrides.json 损坏或缺失，`python3 -c` 的 `except Exception: pass` 吞掉报错 → `whitelist_arr` 为空数组 → `in_whitelist` 永远 false → 方案的"**白名单硬阻**"不变式被静默解除，supervisor 如果误决 SUSPEND manager/router 就真的会被 suspend。建议加一道 `[[ ${#whitelist_arr[@]} -eq 0 ]] && { log "🚨 whitelist empty; refusing to apply"; exit 0; }` 的 fail-safe。|
| F7 | **medium** | `mktemp` 跨文件系统 → `mv` 非原子 | L73 `tmpfile="$(mktemp)"` 默认落 `/tmp`（在容器里一般是 tmpfs）；L151 `mv "$tmpfile" "$DECISIONS"` 的目标在 overlay fs（`$WS/decisions/`）。跨 FS 时 `mv` 降级为 `cp+unlink`，中途收 SIGKILL 会留下半截文件污染 jsonl。改 `tmpfile="$(mktemp -p "$(dirname "$DECISIONS")")"` 即可。|
| F8 | **medium** | 跨零点决策被 stranded | L23 `DATE="$(date +%F)"` 脚本启动时固定；tick 在 23:59 写的决策走 yesterday.jsonl，次日 apply 查 today.jsonl → 旧决策永远不会被 execute。容器级启动 ticker 前可补跑 `supervisor_apply.sh --date $(date -d yesterday +%F)`，或让 apply 扫最近 N 天未 apply 行。|
| F9 | minor | suspend_agent 失败后不重试 | L135-147 失败分支标 `applied_at + apply_result=fail + apply_error`，下一轮跳过。方案 §2.2.2 未显式要求重试，但瞬时失败（tmux 接入瞬断等）被吃掉。建议未来加 `retry_count` + 指数退避（非本轮范围）。|
| F10 | minor | `in_whitelist` 空数组迭代 | `set -u` + `"${whitelist_arr[@]}"` 在 Bash < 4.4 上会 `unbound variable` 报错；容器内 Bash 5.x 无问题，但交叉编译镜像时注意。|

**不是 finding**：
- flock pattern (`flock -w 10 9` + `) 9>"$LOCK"`) 标准且正确。
- `$line` 通过 `python3 -c "..." "$line"` 传 argv[1]，json.loads 安全，无注入。
- 空行保留、malformed-line 日志 + 保留、applied_at 幂等、summary 日志齐全。
- `apply_error` `[:200]` 截断防超大日志污染。

---

### 1.4 `scripts/supervisor_tick.sh`

| # | 等级 | 定位 | 说明 |
|---|---|---|---|
| F11 | minor | cursor 读写无锁 | L54 `open(cursor_file, "w").write(...)` 无 flock；并发触发（ticker + 手动 tick）会互相覆写 cursor，最坏情况漏某 agent 一轮。低频场景可接受。|
| F12 | minor | `"""$TICK_PROMPT"""` 脆 | L86 Python triple-quote 包 `$TICK_PROMPT` 的 shell 展开，若 prompt 文本里出现 `"""` 即破坏 Python 语法。当前 prompt 固定文案安全；若将来让 TICK_PROMPT 可配置化需改 argv 传参。|

**不是 finding**：
- 候选 agent 计算（team.json - never_suspend）正确；cursor 文件损坏/空/agent 已移除三种退化场景都 fallback idx=0。
- 三路径 inject / wake+inject / spawn+inject 与 lifecycle 对齐。
- prompt 里显式 "严禁自己调 suspend_agent" → 决策/执行解耦硬约束落在"最容易违反的一层"（LLM 本身）。

---

### 1.5 `agents/supervisor/workspace/overrides.json`

| # | 等级 | 定位 | 说明 |
|---|---|---|---|
| F13 | minor | `supervisor_ticker` 出现在 `never_suspend` | supervisor_ticker 是 tmux 窗口名，不是 team.json 中的 managed agent；列进去无害但语义混乱。可删（与 supervisor 一项保留即可）。|

**不是 finding**：
- 结构（`never_suspend` + `idle_min_override`）与方案 §5 一致。
- 6 项白名单覆盖了 manager/router/kanban/watchdog/supervisor + 冗余项。

---

### 1.6 `agents/supervisor/workspace/README.md` / `decisions/.gitkeep`

无 finding。README 一句话到位，`.gitkeep` 空文件符合预期。

---

### 1.7 `scripts/docker-entrypoint.sh`（4 处插入点）

| # | 等级 | 定位 | 说明 |
|---|---|---|---|
| F14 | minor | `PREFLIGHT_MSG` 嵌 printf 单引号字面量 | L516-517 `"printf '...$PREFLIGHT_MSG...'"` — 用 `${PREFLIGHT_MSG//\'/}` 剥单引号，但 `$(...)`、反引号、反斜杠未剥。PREFLIGHT_MSG 来自己方 shell 输出可控，风险低但脆弱。建议改用 `printf '%s' "..."` + `%b` 格式或走 tmux send-keys 多行。|

**不是 finding**：
- ①preflight 前置位置在 `spawn_one AGENTS[0] --first` 之前（L498-508）✅
- ②guard 在 watchdog 之后 nohup+setsid 起（L589-594），正确进程组独立 ✅
- ③首轮同步 tick 在 ticker 窗口前（L605-606），冷启动不等 15min ✅
- ④ticker 循环 `tick → apply` 串接（L610）✅
- `PREFLIGHT_FAILED=1` 时跳 worker spawn 走红横幅，lazy/watchdog/ticker/guard 仍起 — 符合方案 §1.3 "只拉 manager" 意图（其他为守护服务，不是业务 worker）。
- init mode 与 start mode 路径保持隔离，preflight 仅在 start 触发。

---

### 1.8 `docs/TROUBLESHOOTING.md` §7 §8

无 finding。§7 覆盖 preflight/guard/cred 三路检查；§8 显式点出"§7 是上游"避免运维在下游打转。First action 步骤具体可执行。

小建议（非 finding）：§8 step 4 引用 "P1-3" 未解释来源，下次迭代可附方案文档链接。

---

## 2. Cross-cutting 问题

| ID | 主题 | 等级 | 涉及文件 |
|---|---|---|---|
| C1 | **overrides 解析失败 ⇒ 白名单失效** | medium | F6（apply.sh）。tick.sh 候选计算也走 `except: pass`，同问题但负面影响小（只是不过滤）。建议两处统一加 loud fail-safe。|
| C2 | **Python 内联 shell 插值模式不统一** | minor | F2（guard）+ tick.sh L30-56/L83-87 + apply.sh L55/L82。团队内应约定"**shell 变量永远走 argv 而不是 Python 源字面量插值**"，至少在两个地方留 comment 说明风险。|
| C3 | **jsonl 文件原子性 / 跨 FS 风险** | medium | F7（apply.sh `mv`）。guard 的 state.json 写（heredoc Python `open(...).w`）也不是 atomic rename — 如果写到一半被 kill，下一轮 `/usage` 读到半截 JSON。可接受（Python 写 dict 到 json 通常 <4KB，原子 pwrite），但要心里有数。|
| C4 | **P0-5 (entrypoint preflight 路径) 未经运行时验证** | 已知/acceptable | coder 报告自述：本轮容器是 P0 前启动，无法 replay entrypoint 前半段；下次 `docker compose up` rebuild 时生效。**qa_smoke 必须跑 `docker compose restart team` 做 P0-5 闭环**。|
| C5 | **notify 洪水 / 无降噪** | medium | F1（guard）。连续过期态下 manager inbox 会被 🚨 淹没，反而掩盖其他告警。|

---

## 3. 按方案 §5 交付清单对齐

| 方案 §5 项 | 交付 | review 结论 |
|---|---|---|
| 1. claude_token_guard.sh | ✅ 存在 + bash -n ok | minor F1/F2/F3/F4 |
| 2. preflight_claude_auth.sh | ✅ 存在 + bash -n ok + exit 0/2/3 正确 | minor F5 |
| 3. supervisor_apply.sh | ✅ 存在 + bash -n ok + flock/幂等/白名单 | medium F6/F7/F8；minor F9/F10 |
| 4. overrides.json | ✅ 6 项 never_suspend + 空 idle_min_override | minor F13 |
| 5. decisions/.gitkeep | ✅ | — |
| 6. workspace/README.md | ✅ | — |
| 7. docker-entrypoint.sh 4 处插入 | ✅ 全部命中 | minor F14 |
| 8. supervisor_tick.sh 重写 | ✅ cursor 模式 + 决策解耦 prompt | minor F11/F12 |
| 9. TROUBLESHOOTING §7 §8 | ✅ 结构完整 | — |

**9/9 全部交付**。

---

## 4. 方案核心不变式对照

| 不变式 | 落地位置 | 强度 |
|---|---|---|
| 决策/执行解耦：supervisor 不调 `suspend_agent` | tick.sh L77 prompt 明令禁止 + apply.sh 独立执行 | **LLM 层 + 脚本层双保险**；LLM 层是软约束（模型可能违反），apply 层是硬约束。可以加固：tick.sh 里 `grep` supervisor 输出有无 `suspend_agent` 调用的 trace，发现则告警（超出本轮范围） |
| 白名单硬阻 | apply.sh L109-122 显式 skip + 标 `apply_skip=whitelist` | **硬**（脚本层），但受 F6 威胁（overrides 解析失败静默绕过） |
| flock 并发保护 | apply.sh L44-48 | **硬**（OS 层）✅ |
| entrypoint preflight gate | entrypoint.sh L496-524 | **硬**（进程层）✅，但未 runtime 验证（C4） |
| apply 幂等 | apply.sh 读 applied_at + 跳过 | **硬**（行级）✅ |

---

## 5. 是否阻塞 qa_smoke

### ❌ 不阻塞 — 建议立即放行

**放行理由**：
1. 所有方案 §5 交付到位，核心机制（preflight/guard/决策/执行/白名单/幂等/cursor）**设计正确且实现无阻塞 bug**。
2. coder 本地 `--once` 自测 + 容器内 3 轮 tick 实测已覆盖正常路径 + 异常路径（malformed/whitelist/idempotent）。
3. 所有 medium finding 都是"长尾稳健性"而非"当前断言失败"：
   - F6 白名单禁用风险只在 overrides.json **损坏时**显现 — qa_smoke 跑的是 happy path
   - F7 跨 FS mv 只在**进程被 SIGKILL** 时才暴 — qa_smoke 不会主动 kill apply
   - F8 跨零点只在**10min 窗口跨 UTC 00:00** 才触发 — qa_smoke 10 分钟窗口能避开
   - C5 notify 洪水只在**凭证持续过期**时表现 — qa_smoke Plan A 先修 OAuth，不会观察到
4. qa_smoke Plan A 的 §6.4 闭环恰好**正向验证** P0-5（`docker compose restart` 后 entrypoint preflight 通过 → worker 正常拉起），覆盖 C4 的已知验证空缺。

### qa_smoke 跑完后必须补修的 medium（不阻塞本轮，但进入 P2 修复清单）

- [ ] F6：overrides.json 解析失败 fail-safe
- [ ] F7：mktemp 同文件系统
- [ ] F8：跨零点决策兜底（ticker 每日 00:05 补跑一次 `--date yesterday`）
- [ ] C5：notify 降噪（同 status 连续命中的冷却窗口）

---

## 6. 质量雷达（1-5，5=优）

| 维度 | 分 | 备注 |
|---|---|---|
| 安全/正确性 | 4 | 无致命缺陷；medium 层 F6/F7 需跟进 |
| 架构/解耦 | 5 | 决策/执行分离 + 白名单硬阻双保险，设计干净 |
| 边界/健壮 | 3 | 跨零点、notify 洪水、parse 失败 fallback 需补 |
| 观测 | 4 | 日志结构统一（timestamp + action + result），state json schema 清晰 |
| 风格/一致 | 4 | shell+python 内联模式局部不统一，其他到位 |

**综合：4.0 / 5.0 — PASS-with-minor**

---

## 7. 建议给 qa_smoke 的 focus 区

运行方案 §6.4 Plan A 时重点观察（非阻塞但值得抓 evidence）：

1. **P0-5 闭环**：`docker compose restart team` 后容器日志必须出 `✅ Claude OAuth preflight: ok (...)`，否则 F4 (guard 无健康校验) 可能掩盖启动失败。
2. **apply.sh 连续 3 轮幂等**：第一轮应 `applied=N`，后两轮必须 `applied=0`。
3. **白名单硬阻可见性**：故意往 decisions.jsonl 里写一行 `{"action":"SUSPEND","agent":"manager"}`，apply 日志必须出 `⚠️ skip manager (in never_suspend whitelist)`。
4. **cross-midnight 如果 smoke 跨 UTC 00:00 跑**：关注 yesterday.jsonl 里未 applied 行是否被 stranded（F8 命中）。

---

*toolsmith · lazywake P0+P1 code review · 2026-04-25*
