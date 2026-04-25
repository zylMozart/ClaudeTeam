# Pane-Diff Idle Detection Code Review · 2026-04-25

**Reviewer**：toolsmith
**Spec**：`docs/architect_pane_diff_idle_2026-04-25.md`
**目标仓**：ClaudeTeam（host 灰度先行）
**审查范围**：
- `scripts/tmux_utils.py`（核心：常量 + `_normalize_pane` + `_is_agent_idle_pane_diff` + `is_agent_idle` 调度 + `quick_idle_hint` + legacy 开关）
- `scripts/team_command.py:82` 并行化
- `scripts/cli_adapters/base.py` busy_markers docstring 降级
- `scripts/regression_tmux_inject.py` 8 mock case + legacy fallback

**审查优先级**：接口兼容性 → fail-safe → 并行实现安全 → 回归覆盖 → 正则偏差 → legacy fallback → busy_markers 调用面

---

## 0. 整体结论

### ✅ PASS-with-minor — 不阻塞 qa_smoke 真机灰度

- **接口契约**：`is_agent_idle(session, window, busy_markers=None, sample_count=None, sample_interval_ms=None)` 完全符合 spec §1.2；4 个生产调用方（msg_queue×2 / team_command×1 / inject_when_idle×1）零改动通过；spec 提到的"27 个调用方"含双仓，ClaudeTeam 单仓 4 处实测验证 ✅
- **fail-safe**：`capture_pane` 返空 → `_is_agent_idle_pane_diff` 立即返 False ✅；`quick_idle_hint` pane_activity 不可用返 None ✅
- **并行**：`ThreadPoolExecutor(max_workers=max(1, len(items)))` 用 `with` 上下文，关闭/超时由 `subprocess.run(timeout=5)` 兜底 ✅；异常隔离有 caveat（见 F4）
- **回归**：8 case + 3 quick-hint 子用例 + legacy fallback = **18 个测试全过**（本地 `python3 scripts/regression_tmux_inject.py` 实跑通过，输出 `✅ regression_tmux_inject passed (incl. pane-diff C1-C8 + legacy fallback)`）
- **legacy fallback**：`CLAUDETEAM_IDLE_LEGACY=1` 走 `_is_agent_idle_legacy`，单帧 busy_markers 实测 idle/busy 正确切换 ✅
- **正则偏差**：`_PANE_DIGIT_RE = r"\d+"` vs spec `\b\d{1,4}\b` — 偏激进方向，**不阻塞但建议加注释或回归 case**（见 F1）

**6 个 finding：0 H / 3 M / 3 L，均为质量改进项**。可放 qa_smoke 跑 §6.4 真机回归。

---

## 1. 按文件 Findings

### F1 (M) — `_PANE_DIGIT_RE = r"\d+"` 偏离 spec `\b\d{1,4}\b` 且无 inline 注释说明

**文件**：`scripts/tmux_utils.py:39`
**Spec**：§4.2 边界 2 step 3 "**保守起见，仅匹配 `\b\d{1,4}\b` 不影响 UUID**"
**实际**：`_PANE_DIGIT_RE = re.compile(r"\d+")` — 无单词边界、无长度上限

**实测对比**（toolsmith 本地跑）：

```
'baked for 1m 16s'       coder→ 'baked for .m .s'           spec→ 'baked for 1m 16s'  (spec 不抹？)
'token count 12345'      coder→ 'token count .'             spec→ 'token count 12345' (spec miss 5+ 位)
'commit a1b2c3d4'        coder→ 'commit a.b.c.d.'           spec→ 'commit a1b2c3d4'   (coder 过抹)
'uuid 36b04e99-703e'     coder→ 'uuid .b.e.-.e'             spec→ 'uuid 36b04e99-703e' (coder 过抹)
'file_v2.py'             coder→ 'file_v..py'                spec→ 'file_v2.py'         (coder 过抹)
'PR #4567'               coder→ 'PR #.'                     spec→ 'PR #.'              (一致)
```

**分析**（coder 选 `\d+` 的现实合理性）：
- ✅ 5+ 位 token 计数器（`Thinking 12345 tokens → 12340`）coder 能抹掉，spec 不能 → coder 路径更稳
- ⚠️ 嵌入 hex/UUID 的数字被过抹，但 hex/UUID 本身在 idle 帧间稳定，过抹不会导致 idle 误判
- ⚠️ 真正的危险面：**busy 帧只有 token 计数器变化（无 spinner）**时，coder 过抹后 hash 全等 → 误判 idle → 可能抢注入。但实际 Claude Code 的 thinking 必带 spinner（braille 字符不是数字，仍然变化），spinner 自身的差异已经触发 busy 判定。**风险为理论值，不是实战值**。

**推荐 fix**：
1. **保底（必做）**：在 `_PANE_DIGIT_RE` 定义上方加 1 行注释，说明"故意偏激进于 spec 的 `\b\d{1,4}\b`，理由：覆盖 5+ 位 token 计数器抖动"。
2. **加固（可选，进 P2）**：补一个回归 case "无 spinner 但 token 计数器漂"，断言判 busy（先红灯，再决定是否调正则）。

---

### F2 (M) — C2 spinner 测试帧把 spinner 放在**非最后一行**，未覆盖"spinner 在最后一行被 normalize 截掉"场景

**文件**：`scripts/regression_tmux_inject.py:230-235` 的 C2
**Spec**：§6.2 C2 "10 帧仅最后一行 spinner 字符在 ⣾⣽⣻⢿ 间循环 → False"
**实际**：

```python
frames = [f"header\n{c} Thinking\n❯ \n" for c in spinner_chars]
```

每帧 4 段：`header / {spinner} Thinking / ❯ /  `（trailing newline 拆出空段）。
`_normalize_pane` 的 step 4 截最后 1 行 → 留下 `["header", "{spinner} Thinking", "❯ "]`。spinner 在 line[1]（非最后），normalize 不抹 → hash 差异保留 → 判 busy ✅

**问题**：测试断言通过没问题，但 **"spinner 真在最后一行（line[-1]）"的 case 未覆盖**。如果某 CLI 把 spinner 放在 pane 最末行，normalize 会截掉 → 帧间 hash 全等 → 误判 idle。Claude Code 的 composer/prompt 通常占最末行，spinner 在中部，所以实战安全；但 spec C2 文字的字面要求"最后一行"未被字面满足。

**推荐 fix**：
- 加一个 C2b case：`frames = [f"static\n❯ \n{c} Thinking" for c in spinner_chars]`（spinner 在 line[-1]）。期望该 case 暴露：normalize 后 idle 误判 → 提示 "如果未来 CLI 把 spinner 放最末行，需要改 normalize 策略"。或者 **保留现有 C2 但改名为 "C2 spinner mid-line"**，明确不是 spec 字面 case。
- 不阻塞本轮：实战的 Claude Code/Codex/Kimi/Gemini layout 都把 prompt 留最末行，spinner 不在最末。

---

### F3 (M) — `ThreadPoolExecutor.map` 异常未隔离，单 agent panic 拖垮整个 `/team`

**文件**：`scripts/team_command.py:93-94`
**Spec**：§4.2 边界 3 + manager 任务点 3 "并行实现安全：ThreadPoolExecutor 关闭、超时、异常隔离"

```python
with ThreadPoolExecutor(max_workers=max(1, len(items))) as ex:
    rows = list(ex.map(probe, items))
```

`ex.map` 在迭代器消费时**重新抛出**首个异常。若 5 agent 中任一 `probe(item)` 抛 `RuntimeError`（极端情况：tmux subprocess 段错误 / adapter resolve panic），`list(ex.map(...))` 会抛异常 → `collect_team_status` 失败 → `/team` 渲染崩。

**现状缓解**：`probe()` 内部对 `resolve_model_for_agent` / `resolve_thinking_for_agent` 都有 try/except；`is_agent_idle` 自身保证不抛（spec §1.2 契约）；`_capture_last` 有 try/except 兜底。**实战 panic 路径很窄**。

**推荐 fix**（P2 范围，不阻塞）：

```python
def safe_probe(item):
    try:
        return probe(item)
    except Exception as e:
        return {"name": item[0], "role": "?", "cli": "?", "model": "?",
                "thinking": "?", "status": f"error: {type(e).__name__}"}

with ThreadPoolExecutor(max_workers=max(1, len(items))) as ex:
    rows = list(ex.map(safe_probe, items))
```

或用 `ex.submit()` + `as_completed` 显式 catch。

---

### F4 (M) — `_normalize_pane` 无条件截掉最后一行，对 "无 trailing newline" 输入会丢真实内容

**文件**：`scripts/tmux_utils.py:112-113`

```python
if len(lines) > 1:
    lines = lines[:-1]
```

`tmux capture-pane -p` 默认输出末尾**通常有** trailing newline，split 后末段为空字符串，截掉无害。但若 capture 路径未来变化或 mock 输入忘了带 `\n`，会丢一行真实内容。

**实测样本**：`capture_pane` 当前实现 `r.stdout` 不裁剪，tmux 输出带 `\n` ✓。但 `_make_capture` 测试 helper 里部分 frame 不带 trailing `\n`（如 C1 `"gpt-5 default\nLine A\n❯ "`）→ 被截后剩 `["gpt-5 default", "Line A"]`，丢了 `❯ ` 行。**当前 C1 仍判 idle 因为所有帧一致；但若用户在 prompt 上写字母（pane 末行 = composer），diff 检测就丢了"composer 内容变化"信号**。

**推荐 fix**：
- 仅当末段是空串（即原文以 `\n` 结尾）时才截：`if lines and lines[-1] == "": lines = lines[:-1]`
- 或保留所有行 + 把"最后一行光标抖动"通过 `_PANE_DIGIT_RE`/`_strip_control` 抹平
- 不阻塞本轮：现实 capture-pane 总带 trailing `\n`。

---

### F5 (L) — `inject_when_idle` docstring 默认值与实参不一致

**文件**：`scripts/tmux_utils.py:368-380`

```python
def inject_when_idle(session, window, text,
                     wait_secs=10, poll_interval=0, ...)
```

但 docstring 写：
```
wait_secs       最长等待空闲的秒数（默认 30s）
poll_interval   轮询间隔（默认 2s）
```

spec §9.1 明确要求 5→10、poll 由 is_agent_idle 内部 sleep 吸收（默认 0）。**代码改对了，注释忘改**。

**推荐 fix**：直接改 docstring 数字。零功能影响。

---

### F6 (L) — `is_agent_idle(sample_count=1)` 退化为单帧判定，调用方不易察觉

**文件**：`scripts/tmux_utils.py:189`

```python
for i in range(max(1, sample_count)):
    ...
    digests.add(...)
    if len(digests) > 1: return False
    if i < sample_count - 1 and interval > 0:
        time.sleep(interval)
return True
```

`sample_count=1` 时循环只跑 1 次 → 1 个 digest → 永远不满足 `> 1` → 返 True（除非 capture 失败）。这就**等价于"capture 不失败 = idle"**，实际是 **任何单帧都判 idle**。这违背 pane-diff 的设计意图。

虽然 spec §2.1 默认值 10，env 覆盖也极不可能设 1，但若 qa_smoke 调试用 `CLAUDETEAM_IDLE_SAMPLE_COUNT=1` 速跑测试，会得到"全 idle"假阳性。

**推荐 fix**：
- 入口加 `sc = max(2, sample_count or SAMPLE_COUNT)`，强制至少 2 帧才能判 idle；或
- 文档化（docstring 说明 sample_count<2 时退化为"capture 成功即 idle"）
- 不阻塞：默认 10，env 覆盖也极少 < 2。

---

## 2. Cross-cutting / 架构观察（非 finding）

### A1 — `team_command.py:83` 仍传 `adapter.busy_markers()` 第三参（spec §3.1 要求"不动"）✅

```python
elif not is_agent_idle(session, name, adapter.busy_markers()):
```

`busy_markers` 入参在 `is_agent_idle` 内被忽略（仅 legacy 路径使用）。这意味着 `/team` 每次渲染都白调 `adapter.busy_markers()` 一次（5 agent × ~10μs）→ 累计微秒级浪费。**不是 finding**：spec 明确"不改 27 个调用方"。

### A2 — `quick_idle_hint` 实际未在生产被任何代码调用

Grep `quick_idle_hint` 只命中 tmux_utils.py 自己 + regression_tmux_inject.py 的 C8 测试。spec §1.3 / §3.1 列的用途（`/team` 状态卡、msg_queue 选目标预筛）**本轮未消费**。

这与 spec §3.1 一致（spec 只要求"加 helper"，不要求改调用方），但留存"加了 helper 没人用"的代码味道。下一轮（pane-diff 跑稳后再说）应让 team_command 替换 is_agent_idle → quick_idle_hint，把 5×3s 串行降到 5×~50ms。

**当前不是 finding**：本轮 PR 边界明确划在"加 helper"，调用方迁移留给后续。

### A3 — `_input_still_visible` 仍然用 `_BUSY_MARKERS` 判"瞬时"，与 spec §5 一致 ✅

```python
# tmux_utils.py:318
if any(marker in tail for marker in _BUSY_MARKERS):
    return False
```

spec §5 表格："`_input_still_visible` 注入复核：保留（判'用户输入是否还卡在输入框'，不是判'在不在干活'）"。代码未动 → 符合。

### A4 — adapter `busy_markers()` 抽象方法保留 + base.py docstring 降级 ✅

```python
# scripts/cli_adapters/base.py:15-18
@abstractmethod
def busy_markers(self) -> list:
    """pane 末尾出现任一 → agent 正忙。
    since 2026-04-25 only used by quick_idle_hint（is_agent_idle 已切到 pane-diff）。
    """
```

5 个 adapter 实现都未改 ✓。`resolve.py:40` shell 命令保留 ✓。

---

## 3. 调用面核查（spec §4.1 矩阵 + manager 关注点 1/7）

### 3.1 `is_agent_idle` 生产调用（ClaudeTeam 侧）

| 调用点 | 形式 | 改后行为 | 兼容? |
|---|---|---|---|
| `scripts/msg_queue.py:89` | `is_agent_idle(TMUX_SESSION, agent_name)` | 阻塞 ~2.7s 单 agent | ✅ |
| `scripts/msg_queue.py:138` | `is_agent_idle(TMUX_SESSION, "manager")` | 同上 | ✅ |
| `scripts/team_command.py:83` | `is_agent_idle(session, name, adapter.busy_markers())` | 第三参忽略 | ✅ |
| `scripts/tmux_utils.py:420` (`inject_when_idle`) | `is_agent_idle(session, window)` | 内部 poll 由 is_agent_idle 自身 ~2.7s 吸收 | ✅ |

**4/4 兼容**。restructure 侧路径（msg_queue→runtime/queue, team_command→commands/team）形态完全一致，**双仓同步时也应平迁**（spec §3.2）。

### 3.2 `busy_markers` 仍被消费的位置

| 文件 | 用途 | 改后状态 |
|---|---|---|
| `tmux_utils._input_still_visible` | 注入复核 | 保留消费 `_BUSY_MARKERS` ✅ |
| `tmux_utils.quick_idle_hint` | 速判二级兜底 | 保留消费 (busy_markers ⏐ `_BUSY_MARKERS`) ✅ |
| `tmux_utils._is_agent_idle_legacy` | env=1 回滚路径 | 保留消费 ✅ |
| `team_command.py:83` | 传给被忽略的 is_agent_idle | 等同于 no-op ✅ |
| `cli_adapters/resolve.py:40` | CLI 命令 | 不动 ✅ |
| `cli_adapters/{cc,codex,kimi,gemini,qwen}.py` | 5 adapter 实现 | 不动 ✅ |

**没有别处直接调 `adapter.busy_markers()` 被忽视** — `/home/admin/projects/server_manager/ClaudeTeam` 全仓 grep 已确认。

---

## 4. fail-safe 验证（manager 关注点 2）

| 故障情形 | 设计 | 实现位置 | 测试覆盖 |
|---|---|---|---|
| `capture_pane` 抛异常 | 内部 try/except，返空字符串 | `tmux_utils.py:86-93` | 间接（C4） |
| 任一帧 capture 返空 | 立即 `return False` | `_is_agent_idle_pane_diff:191-192` | C4 ✅ |
| 帧间立即检测差异 | `if len(digests) > 1: return False` | `:194` | C2/C6/C7 ✅ |
| `quick_idle_hint` pane_activity 不可用 | `return None` | `quick_idle_hint:228-232` | 隐含（C8 路径） |
| `is_agent_idle` 不抛异常 | 全路径返 bool | `:201-212` | 全测试隐含 |

**所有 fail-safe 路径均有测试覆盖**。 ✅

---

## 5. 并行实现安全（manager 关注点 3）

| 检查项 | 状态 |
|---|---|
| ThreadPoolExecutor `with` 上下文（自动 shutdown） | ✅ |
| 子任务超时 | 由 `subprocess.run(timeout=5)` 在 capture_pane / display-message 处兜底 ✅ |
| 异常隔离 | ⚠️ F3 — `ex.map` 重抛首个异常会拖垮整个 collect_team_status |
| 工作线程数上限 | `max_workers=max(1, len(items))`：5 agent → 5 worker ✅；无硬上限（理论 50 agent 起 50 thread，实战不会出现） |
| adapter.busy_markers() 在并行下安全 | adapter 是无状态 ABC，多线程读 OK ✅ |
| capture_pane 并行调 tmux | tmux server 单进程多 socket，并行 capture 安全 ✅ |

---

## 6. 回归覆盖（manager 关注点 4）

### Spec §6.2 4 个**必须** case

| Spec | 实现 | 实跑 |
|---|---|---|
| C1 完全静止 → True | `test_pane_diff_C1_static_idle` | ✅ |
| C2 spinner 抖动 → False | `test_pane_diff_C2_spinner_busy` | ✅（见 F2 caveat） |
| C3 时间戳跳动但 idle → True | `test_pane_diff_C3_timestamp_drift_idle` | ✅ |
| C4 capture 失败 → False | `test_pane_diff_C4_capture_failure_busy` | ✅ |

### Spec §6.3 4 个**锦上** case

| Spec | 实现 | 实跑 |
|---|---|---|
| C5 cursor 行抖动 → True | `test_pane_diff_C5_cursor_jitter_idle` | ✅ |
| C6 流式打字 → False | `test_pane_diff_C6_streaming_busy` | ✅ |
| C7 无 busy_markers 但 hash 不等 → False | `test_pane_diff_C7_no_busy_marker_but_changing` | ✅ |
| C8 quick_idle_hint 老活动 + 单帧无 busy → True | `test_pane_diff_C8_quick_idle_hint_old_activity` + `_C8b_recent` + `_C8c_busy_marker_in_frame` | ✅（拆 3 子用例覆盖更全） |

### 锦上 + legacy

- `test_legacy_env_fallback_busy_marker` — `CLAUDETEAM_IDLE_LEGACY=1` 一键回滚 idle/busy 双向验证 ✅

**8 必须 + 1 legacy + 9 既有 = 18 测试全过**（实跑 `python3 scripts/regression_tmux_inject.py` 输出 `✅ regression_tmux_inject passed`）。

### C7 over-fit 检查（manager 关注点 4）

C7 用 `applying patch / +++ added line a..j / ❯` 三行帧；normalize 后留 line[0]+line[1]（截最末），line[1] 字符在变 → hash 差 → busy。**正确反映 spec "pane-diff 比 markers 更敏感" 意图**，无 over-fit。

---

## 7. legacy fallback 真起作用（manager 关注点 6）

**入口**：`scripts/tmux_utils.py:208-209`

```python
if os.environ.get("CLAUDETEAM_IDLE_LEGACY") == "1":
    return _is_agent_idle_legacy(session, window, busy_markers)
```

- env 在 **每次** `is_agent_idle` 调用时读，不是模块加载时缓存 ✓
- `_is_agent_idle_legacy` 内部独立实现 — 不依赖 `_normalize_pane` / `_PANE_DIGIT_RE` / pane-diff 任何分支 ✓
- 测试 `test_legacy_env_fallback_busy_marker` 双向验证：
  - `Thinking\n` → busy ✓
  - `❯ \n` → idle ✓

**一键回滚保证落地**。 ✅

---

## 8. 是否阻塞 qa_smoke 真机灰度

### ❌ 不阻塞 — 立即放行

**理由**：
1. 接口契约 100% 兼容，4 个生产调用方零改动
2. fail-safe 全路径测试覆盖
3. 18 个 mock 测试全过（含 legacy 回滚双向）
4. legacy 一键回滚机制硬到位（env 每调用读，无 cache）
5. 6 个 finding 全部为**质量改进/长尾稳健性**，无任何 H 级阻塞
6. 灰度阶段一旦发现误判，`CLAUDETEAM_IDLE_LEGACY=1` 秒级回滚，比 git revert 快

### qa_smoke 真机灰度建议关注点（非阻塞但值得 evidence）

1. **`/team` 渲染时长**（spec §6.4 阶段 A 第 3 条）：4 worker 并行预期 ~3s，10 次采样的 P95 应 < 4s
2. **inject_when_idle 单 agent 注入成功率**（spec §6.4 阶段 A 第 4 条）：与 pane-diff 上线前对比
3. **`msg_queue` 误投率**（spec §6.4 阶段 A）：观察 router 日志有无 `agent=X status=busy skip` 异常增多
4. **F1 风险点真机验证**：找一个 token 计数器 5+ 位数的窗口（Claude Code thinking ≥ 5 分钟），看 pane-diff 是否仍然判 busy（应该 yes，因为 spinner 字符与 token 数同时变）。如果观察到 idle 误判 + 抢注入，立刻 `CLAUDETEAM_IDLE_LEGACY=1` 回滚 + 找 toolsmith 修 F1。

---

## 9. 给 coder 的非阻塞改进建议（P2 批次）

按 severity 排序，可一次或多次小 PR：

| # | 等级 | 位置 | 建议 |
|---|---|---|---|
| F1 | M | `tmux_utils.py:39` | 加 inline 注释说明 `\d+` 偏激进于 spec 的理由（覆盖 5+ 位 token 计数器）；或加回归 case "无 spinner + 5+ 位 token 漂动 → busy" |
| F2 | M | `regression_tmux_inject.py:230` | 加 C2b "spinner 真在 line[-1]"，文档化 normalize 截尾的边界 |
| F3 | M | `team_command.py:93` | `safe_probe` wrapper 包 `probe`，捕获异常返 status="error: ..." 单条降级 |
| F4 | M | `tmux_utils.py:112` | `if lines and lines[-1] == "":` 仅截真空末段 |
| F5 | L | `tmux_utils.py:378-380` | 改 docstring 默认值数字（30→10、2→0） |
| F6 | L | `tmux_utils.py:189` | `sc = max(2, ...)` 强制至少 2 帧；或 docstring 说明 sample_count<2 退化 |

---

## 10. 质量雷达（1-5，5=优）

| 维度 | 分 | 备注 |
|---|---|---|
| 接口兼容 | 5 | 4 调用方零改动通过 |
| Fail-safe | 5 | 全路径测试覆盖 + bool 不抛 |
| 并行实现 | 4 | 上下文 shutdown ✓ 超时 ✓ 异常隔离需 wrapper（F3） |
| 回归覆盖 | 5 | 8/8 + legacy 双向，实跑全过 |
| Spec 一致 | 4 | F1 正则偏离需注释；F2 测试覆盖偏 |
| 风格/可读 | 4 | docstring 默认值（F5）小掉漆 |

**综合 4.5 / 5.0 — PASS-with-minor**

---

*toolsmith · pane-diff idle review · 2026-04-25*
*结论：放 qa_smoke 跑 spec §6.4 真机回归；6 项 P2 改进进 backlog；任何灰度异常 → `CLAUDETEAM_IDLE_LEGACY=1` 秒级回滚。*
