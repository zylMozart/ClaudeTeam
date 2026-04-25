# Pane-Diff Idle 检测重设（架构 spec）· 2026-04-25

**作者**：architect
**触发**：老板 03:12 提议、12:51 定型（窗口=3s）、13:00+ 走 architect→coder→review→qa_smoke 流水线
**目标**：把"在不在干活"判定从 "每 CLI 一份 busy_markers 关键词正则" 改为 **"3 秒内连续 10 帧画面 hash 全等 = idle，否则 busy"**
**范围**：本文件只出 spec，不写代码。

---

## 0. TL;DR

- **新契约**：`is_agent_idle(session, window)` 阻塞 3 秒采集 10 帧 → 全等返 `True`，有差异返 `False`。**不再吃 `busy_markers` 入参**（保留位置参数兼容旧调用，但忽略）。
- **常量可调**：`SAMPLE_COUNT=10`、`SAMPLE_INTERVAL_MS=300`、`HASH_NORMALIZE=True`，从 env 读 override 兜底。
- **接口形态变成两层**：
  - `is_agent_idle(...)` —— 严格 3 秒采样（精度优先，单调用 3s）
  - `quick_idle_hint(...)` —— 单帧 + tmux 原生 `pane_activity` 时间戳（速度优先，单调用 <100ms，给 `/team` 这种批量场景用）
- **批量场景**（`/team`、msg_queue 多 agent 派单、inject_when_idle 内部轮询）必须改 **并行** 调用，不能串行 5×3s=15s。
- **busy_markers 的命运**：**不删，只降级**。adapter 上的 `busy_markers()` 保留，作为 `quick_idle_hint` 的二级信号；is_agent_idle 不再读它。
- **灰度顺序**：先 ClaudeTeam（host 跑 4 worker）跑 30 分钟实测，再同步 restructure（容器内 5 agent）。

---

## 1. 接口契约

### 1.1 现状（改前）

ClaudeTeam `scripts/tmux_utils.py:146` / restructure `src/claudeteam/runtime/tmux_utils.py:42`：

```python
def is_agent_idle(session, window, busy_markers=None):
    """capture-pane 一次 → 取 last 3 行 → 任一 busy_markers 出现 → busy；否则 idle。"""
    content = capture_pane(session, window)
    if not content:
        return False
    markers = busy_markers if busy_markers is not None else _BUSY_MARKERS
    last_lines = "\n".join(content.rstrip().split("\n")[-3:])
    for busy in markers:
        if busy in last_lines:
            return False
    return True
```

### 1.2 改后契约

```python
def is_agent_idle(session, window, busy_markers=None,
                  sample_count=None, sample_interval_ms=None):
    """
    通过连续多帧画面 hash 比较判定空闲。

    入参（**全部可选**，常量从模块级或 env 取默认）:
      session, window      — 同前
      busy_markers         — **保留位但忽略**（向后兼容，未来移除）
      sample_count         — 采样帧数（默认 10）
      sample_interval_ms   — 帧间隔毫秒（默认 300）
    总耗时 = (sample_count - 1) * sample_interval_ms ≈ 9 * 300ms = 2.7s

    返回:
      True  — 全部帧 hash 等同（视为空闲，可以安全注入）
      False — 至少一帧不同（视为忙碌）/ 窗口不存在 / capture 失败

    实现细节:
      - 采集 10 帧 capture-pane 输出（保持窗口名/坐标不变）
      - 每帧先做 _normalize_pane(text) 剥离干扰位（见 §4.2）
      - hashlib.sha1(normalized_bytes).digest() 比较
      - 帧间 time.sleep(sample_interval_ms / 1000)
      - 任意一次 capture_pane 返空字符串 → 立即返 False（fail-safe，宁可等也不抢注入）
    """
```

**入参出参契约**：
- `(session, window)` 必填，二者类型不变（str）
- `busy_markers` 位置/关键字保留 — 老调用方（msg_queue.py、team.py、inject_when_idle）不需要改一行就能跑
- 返回类型保持 `bool`
- **不抛异常**：capture 失败、tmux 超时、窗口消失全部内化为 `return False`

### 1.3 新加 helper：`quick_idle_hint`

为不愿意等 3 秒的批量场景准备：

```python
def quick_idle_hint(session, window, busy_markers=None, max_age_secs=2):
    """
    速度优先的 idle 速判（<100ms），用 tmux 原生 pane_activity 时间戳 + 单帧 busy_markers 兜底。

    返回:
      True  — pane_activity 距今 > max_age_secs 且单帧不含 busy_markers（视为可能空闲）
      False — pane 刚有活动 / 单帧含 busy_markers / 检测失败
      None  — 检测不出（极少见，调用方自己决定 fallback）
    """
```

**用途**：`/team` 状态卡、msg_queue 选目标 agent 时的预筛、面板渲染。**它不是注入前置**——任何要 `send-keys` 的代码必须最终调 `is_agent_idle` 严格采样。

`pane_activity` 已被 ClaudeTeam `tmux_utils.py:272` 在 `check_agent_alive` 里使用过，是稳定可用的 tmux 内置时间戳。

### 1.4 thinking 注释

`busy_markers` 入参从 `is_agent_idle` 的逻辑里被忽略，但参数保留 — 这是**显式 deprecation**，不动 27 处调用方。
未来（下一轮或下下轮）可加 DeprecationWarning，再下一轮真删。本轮**绝不**删入参，免得造成 27 处编译/导入错误。

---

## 2. 阈值常量

### 2.1 默认值

| 常量                         | 默认  | 单位  | 说明                                            | env 覆盖名                              |
|------------------------------|-------|-------|-------------------------------------------------|-----------------------------------------|
| `SAMPLE_COUNT`               | 10    | 帧    | 总采样帧数                                       | `CLAUDETEAM_IDLE_SAMPLE_COUNT`          |
| `SAMPLE_INTERVAL_MS`         | 300   | ms    | 帧间隔；老板要求"3 秒"≈ 9 × 300ms = 2.7s        | `CLAUDETEAM_IDLE_SAMPLE_INTERVAL_MS`    |
| `HASH_NORMALIZE`             | True  | bool  | 是否走 _normalize_pane 剥离时间戳/光标          | `CLAUDETEAM_IDLE_NORMALIZE` (0/1)       |
| `QUICK_HINT_MAX_AGE`         | 2.0   | s     | quick_idle_hint 的 pane_activity 阈值           | `CLAUDETEAM_IDLE_QUICK_AGE_S`           |

### 2.2 调整建议

- 是否需要可调？**需要，但保守开放**：env 覆盖足够，不引入 runtime_config.json 字段（避免新增 schema）。
- 调整时机：本 spec 写定后，灰度 30 分钟若发现 4 worker 中 ≥1 出现误判（idle 判成 busy 或反向），先调 `SAMPLE_INTERVAL_MS` 而不是 `SAMPLE_COUNT`（间隔更敏感，且改帧数破坏 3s 总时长承诺）。
- 总时长承诺：保持 ~3s，老板 12:51 已定。

---

## 3. 落点清单（双仓）

### 3.1 ClaudeTeam（host 跑团队，灰度先行）

| 文件                                 | 动作                                                                                          |
|--------------------------------------|-----------------------------------------------------------------------------------------------|
| `scripts/tmux_utils.py`              | 改 `is_agent_idle` 实现 + 新增 `quick_idle_hint`；保留 `_BUSY_MARKERS`（quick_idle_hint 用） |
| `scripts/tmux_utils.py:235`          | `_input_still_visible` 仍引用 `_BUSY_MARKERS`，**不动**（它判的是单帧瞬间状态，不是"在干活"） |
| `scripts/cli_adapters/base.py`       | `busy_markers()` 抽象方法**保留**，docstring 加一句"自 2026-04-25 起仅供 quick_idle_hint 使用" |
| `scripts/cli_adapters/{cc,codex,kimi,gemini,qwen}.py` | **不动**。busy_markers 还有 quick_idle_hint 在用                                  |
| `scripts/cli_adapters/resolve.py`    | 不动（CLI 暴露 busy_markers 命令保留）                                                        |
| `scripts/team_command.py:82`         | 不动。`is_agent_idle(session, name, adapter.busy_markers())` 第三参被忽略，逻辑不变（向后兼容） |
| `scripts/msg_queue.py:89, 138`       | 不动。两处都是单 agent 单调用，3s 可接受                                                      |
| `scripts/regression_tmux_inject.py`  | 加 4 个新 case（见 §6）                                                                       |

### 3.2 restructure（容器跑团队，灰度跟进）

| 文件                                                | 动作                                          |
|-----------------------------------------------------|-----------------------------------------------|
| `src/claudeteam/runtime/tmux_utils.py`              | 与 ClaudeTeam tmux_utils.py 同步改            |
| `src/claudeteam/cli_adapters/base.py` 等            | 与 ClaudeTeam adapter 同步改 docstring        |
| `src/claudeteam/commands/team.py:76`                | 不动                                          |
| `src/claudeteam/runtime/queue.py:94, 140`           | 不动                                          |
| `src/claudeteam/messaging/router/daemon.py`         | 不动（间接通过 queue.py 用 is_agent_idle）    |
| `tests/regression_message_sanitizer.py`             | 跑一遍确认没回归                              |
| `tests/static_public_contract_check.py`             | 跑一遍 — 公共接口签名兼容（busy_markers 仍在） |
| `tests/compat_scripts_entrypoints.py`               | 跑一遍                                        |

### 3.3 双仓同步铁律

- 改完 ClaudeTeam → 灰度跑 30 分钟（见 §7）→ **diff 应用**到 restructure（不是 cherry-pick，是逐字对照）
- 容器侧用 `docker cp` 热更，不重建镜像
- 两侧的常量默认值必须一致（SAMPLE_COUNT/SAMPLE_INTERVAL_MS）
- 提交两个 PR（不合并），让 review 能逐仓看 diff

---

## 4. 调用方核查 + 边界情况

### 4.1 现有调用方矩阵

ClaudeTeam 侧：

| 文件:行                                | 调用形式                                            | 改后行为                                     | 风险                                  |
|----------------------------------------|-----------------------------------------------------|----------------------------------------------|---------------------------------------|
| `tmux_utils.py:337` (`inject_when_idle`) | `is_agent_idle(session, window)` 在 poll 循环里     | poll 间隔变成 3s 一次（可接受，wait_secs=5 默认会多一次）；建议把 inject 内 poll 间隔从 0.5s 改为 0；让 is_agent_idle 自身的 3s 充当 sleep | wait_secs=5 时只有 1.6 次 poll 机会；要么放宽 wait_secs 默认到 10，要么减少 poll 次数 |
| `msg_queue.py:89`                      | `is_agent_idle(TMUX_SESSION, agent_name)`           | 派单前阻塞 3s，单 agent 一次，可接受          | 队列吞吐：5 agent×3s=15s 派完一轮，但 inbox 多消息合并即可缓解 |
| `msg_queue.py:138`                     | `is_agent_idle(TMUX_SESSION, "manager")`            | manager 派单前阻塞 3s，可接受                | 同上                                  |
| `team_command.py:82` (`/team` 卡片)     | 串行 5 个 `is_agent_idle(...)`                       | **必须改并行**：5 agent 同时跑 → 整体仍 ~3s | 不改就 15s 渲染，UX 崩                |

restructure 侧路径同（只是文件位置不同），调用形态完全一致。

### 4.2 边界情况

#### 边界 1 · 窗口刚被切换 / capture-pane 头几帧异常

- **现象**：tmux 窗口刚 attach 或切换，第一帧 capture 可能是空字符串或包含 alternate-screen 残影。
- **对策**：`capture_pane()` 失败一次 → `is_agent_idle` 立即返 `False`。即"宁可错判忙碌，也不抢注入"，符合 fail-safe 原则。

#### 边界 2 · cursor blink / 时间戳行 / 旋转 spinner 在原本 idle 的 pane 上跳

- **现象**：tmux capture-pane 输出里：(a) 可能含 ANSI 光标定位序列（不同帧位置不同）；(b) Claude Code 状态栏底部有 token 计数 / 模型名 / 时间秒数；(c) 即便实际上 idle，spinner 字符还停留在历史输出里。
- **对策**：实现 `_normalize_pane(text)`：
  1. 剥 ANSI 控制字符（已有 `_strip_control`）
  2. 去掉每行末尾空格
  3. **去掉所有数字串**（保守起见，仅匹配 `\b\d{1,4}\b` 不影响 UUID）— 把 `Baked for 1m 16s` 与 `Baked for 1m 17s` 归一
  4. **截掉最后 1 行**（cursor 行往往这里跳；保留前 N-1 行做 hash 已经足够区分 idle/busy）

  把 hash 算在归一化后的 bytes 上。
- **trade-off**：归一化越激进，假阴性（明明 busy 但 hash 一致）风险越高；越保守，假阳性（明明 idle 但 hash 不一致）越高。**默认走"保守归一化"**（剥 ANSI + 去末行 + 去数字），覆盖 90%；剩下的让 quick_idle_hint 二级兜底。

#### 边界 3 · /team 等批量场景的 N×3s

- **现象**：5 agent 串行调 is_agent_idle → 15s。
- **对策**：调用方层面用 `concurrent.futures.ThreadPoolExecutor` 把 N 个 is_agent_idle 并行，整体仍 ~3s。
- 是 **调用方** 的责任，不是 is_agent_idle 自己的责任 — is_agent_idle 保持单线程同步语义最简单。
- 这一点要写进 spec 的"调用方迁移指南"小节，coder 改 team.py 时按这个走。

#### 边界 4 · inject_when_idle 的内层 poll

- 当前实现：`while elapsed < wait_secs: if is_agent_idle(...): inject; else: sleep(poll_interval)`
- 改后：每次 is_agent_idle 自带 3s。建议：
  - 把 `poll_interval` 默认值改为 0
  - 把 `wait_secs` 默认值从 5 提到 10（容纳至少 3 次采样）
  - 第一次 fail 后立即重试，不再 sleep（is_agent_idle 内部已经"sleep" 3s 了）

---

## 5. busy_markers 的去留（关键澄清）

| 用途                                | 现状                         | 改后                              |
|-------------------------------------|------------------------------|-----------------------------------|
| `is_agent_idle` 主判断              | 单帧 last 3 行匹配           | **不再用**（pane-diff 替代）      |
| `quick_idle_hint` 速判（新）         | —                            | **保留使用**（单帧瞬时兜底）      |
| `_input_still_visible` 注入复核     | 单帧 last 3 行匹配           | **保留**（判"用户输入是否还卡在输入框"，不是判"在不在干活"，语义不同） |
| adapter `busy_markers()` 抽象方法    | 必须实现                     | **保留必须实现**                  |
| CLI `resolve.py busy_markers`       | 暴露给 shell                 | **保留**                          |

**不删 busy_markers 的理由**：
1. `_input_still_visible` 是注入复核，判的是"输入框里是否还残留没回车的字"，需要单帧瞬时判断；3s pane-diff 不适用。
2. `quick_idle_hint` 给批量 UI 用，不能也阻塞 3s。
3. 删除引发的破坏面（5 个 adapter + 1 个 base + 1 个 resolve + 多处调用）远大于保留。
4. 让 pane-diff 和 busy_markers **共存且分工明确** 比"一刀切"安全。

---

## 6. 回归测试设计

新增 `tests/regression_pane_diff_idle.py`（双仓各一份；ClaudeTeam 现有 `scripts/regression_tmux_inject.py` 同步加 case）。

### 6.1 Test Harness 设计

mock `capture_pane`（不真起 tmux）：

```python
class FakeCapture:
    def __init__(self, frames):
        self.frames = list(frames)
    def __call__(self, session, window):
        return self.frames.pop(0) if self.frames else self.frames[-1]
```

`monkeypatch.setattr(tmux_utils, "capture_pane", FakeCapture([...]))` 注入 10 帧。

### 6.2 必须的 4 个 case

| Case                          | 输入帧                                        | 期望返回 | 断言点                          |
|-------------------------------|-----------------------------------------------|----------|---------------------------------|
| **C1 完全静止**               | 10 帧字符串完全相同（含 prompt `❯ `）         | `True`   | hash 全等                       |
| **C2 spinner 抖动**           | 10 帧仅最后一行 spinner 字符在 ⣾⣽⣻⢿ 间循环   | `False`  | 即使最后行被剥也会被 normalize 后 hash 不等（spinner 字符不是数字） |
| **C3 时间戳跳动但 idle**       | 10 帧仅状态栏底部秒数从 1m 16s → 1m 17s       | `True`   | normalize 去数字后 hash 全等    |
| **C4 capture 失败**            | 第 5 帧返回空字符串                           | `False`  | 立即 fail-safe 返回             |

### 6.3 锦上的 4 个 case

| Case                                   | 输入                                    | 期望         |
|----------------------------------------|-----------------------------------------|--------------|
| **C5 cursor 行抖动**                    | 仅最后一行 cursor 位置跳，前面相同       | `True`       |
| **C6 流式打字**                        | 每帧最后一行字符串递增                   | `False`      |
| **C7 单帧不含 busy_markers 但 hash 不等** | 流式输出但内容不带 spinner 字符          | `False`（pane-diff 比 busy_markers 更敏感） |
| **C8 quick_idle_hint** 新 helper        | mock pane_activity 老于阈值 + 单帧无 busy | `True`       |

### 6.4 真机回归（qa_smoke 跑）

mock 测试通过后 qa_smoke 再上 host：
1. ClaudeTeam attach 一个 worker_cc 窗口，发"hello 等 5s"，is_agent_idle 应在前 5s 返 False，5s 后开始返 True；
2. 同窗口让 CC 走一段 long thinking，is_agent_idle 应稳定返 False；
3. /team 渲染时长 < 4s（5 agent 并行）；
4. msg_queue 派单延迟增加 ≤ 3s（单 agent）。

---

## 7. 灰度策略

老板的"先 ClaudeTeam 跑 30 分钟看 4 worker 实测"按下面落：

### 7.1 阶段 A · ClaudeTeam 灰度（30min）

1. coder 在 ClaudeTeam 改完 → 跑 mock 回归（C1-C8 全过）
2. host 侧重启 router + 4 worker（不动 manager）
3. 30 分钟内观察：
   - 4 worker 各发 1 条业务消息 + 1 段 long thinking → 看 inbox 投递时机
   - `/team` 渲染 ≤ 4s（10 次采样）
   - msg_queue 没有"派给 busy agent"的误投（看 router 日志 `agent=X status=busy skip`）
4. 期间任一回归点出错（误判率 > 5%，或派单延迟 > 5s）→ 立即回滚（git revert + 重启）

### 7.2 阶段 B · restructure 同步（30min）

阶段 A 通过后：
1. 把 ClaudeTeam 的 diff 在 restructure 上**逐字对应**应用（不要 cherry-pick，因为路径不同）
2. `docker cp` 进容器 + 重启对应 tmux 窗口（不 rebuild 镜像）
3. 同样 30min 观察
4. qa_smoke 跑 §6.4 真机回归

### 7.3 阶段 C · 提交

两阶段都通过后 → 两份 PR（一仓一个）→ review → merge。

### 7.4 回滚开关

为防灰度翻车，**保留旧实现**作为开关：

```python
def is_agent_idle(...):
    if os.environ.get("CLAUDETEAM_IDLE_LEGACY") == "1":
        return _is_agent_idle_legacy(session, window, busy_markers)
    return _is_agent_idle_pane_diff(session, window, ...)
```

灰度阶段如出问题：`CLAUDETEAM_IDLE_LEGACY=1` 立即降级到旧逻辑，无需 git revert。
跑稳一周后下一轮删 legacy 路径。

---

## 8. 风险标记

### 8.1 删 busy_markers 的连带影响（结论：不删，无影响）

- 选择"保留 busy_markers"已规避大半风险（见 §5）
- 仅 is_agent_idle 内部不再用，27 个调用方一行不改

### 8.2 lazy-wake 是否受影响

- `agent_lifecycle.sh::wake_agent` **不调** is_agent_idle
- `supervisor_tick.sh` 通过 `_lifecycle_pids_for_agent` 判进程在不在，**不调** is_agent_idle
- `inject_when_idle` 间接调 → 行为变慢（从单帧到 3s），但是 wake 后 supervisor 喂 prompt 慢 3s 不影响功能，只影响首条注入延迟
- **结论**：lazy-wake 链路无破坏面

### 8.3 supervisor 自动 SUSPEND 是否受影响

- supervisor 自己写决策 jsonl，靠的是它读 inbox/pane/状态表，**不直接调** is_agent_idle
- supervisor_apply.sh 调 suspend_agent 也不查 is_agent_idle
- **结论**：和今天上午写的 lazy-wake fix plan §2 协同无冲突

### 8.4 kanban_sync / router daemon

- router 通过 queue.py 间接调，单 agent 单调用，3s 可接受
- kanban_sync 不调 is_agent_idle（只读状态表 + bitable）
- **结论**：无破坏面

### 8.5 性能/资源

- `capture-pane` 每秒 ~3.3 次（10/3s），4 worker 并发约 13 次/s，tmux 服务器单核负载 < 1%。**安全**
- hash 计算 sha1 over 10×几 KB → 微秒级。**安全**

### 8.6 真正的开放风险

| 风险                                                          | 影响     | 缓解                                    |
|---------------------------------------------------------------|----------|-----------------------------------------|
| normalize 太激进 → idle 误判（漏判 busy）→ 抢注入             | 中       | C2 用例确保 spinner 抖动也能识别        |
| normalize 太保守 → busy 误判（错过 idle）→ 派单延迟           | 低       | 调小 SAMPLE_INTERVAL_MS                 |
| /team 并行没改 → UI 卡 15s                                    | 中       | 把"调用方迁移指南"明示到 PR 描述        |
| inject_when_idle wait_secs=5 默认不够 → 经常超时强注入        | 中       | spec 建议默认改 10s；coder 改两处       |
| pane_activity 在某些 tmux 版本不可用                          | 低       | quick_idle_hint 检测失败时返 None       |

---

## 9. 给 coder 的清单（仅本轮 PR）

### 9.1 ClaudeTeam 双仓步骤

仅 ClaudeTeam（restructure 后续）：

| # | 文件                              | 动作                                                                                  |
|---|-----------------------------------|---------------------------------------------------------------------------------------|
| 1 | `scripts/tmux_utils.py`           | 加常量 + `_normalize_pane` + `_is_agent_idle_pane_diff` + 改 `is_agent_idle` 调度 + `quick_idle_hint` |
| 2 | `scripts/tmux_utils.py:285`       | `inject_when_idle` 默认 `wait_secs` 5→10；poll 由内层吸收                              |
| 3 | `scripts/team_command.py:82`      | 把 5 个 agent 的 is_agent_idle 调用并行（ThreadPoolExecutor）                         |
| 4 | `scripts/cli_adapters/base.py`    | docstring 加一句 "since 2026-04-25 only used by quick_idle_hint"                      |
| 5 | `scripts/regression_tmux_inject.py` | 加 8 个 case（C1-C8）                                                                |

restructure（阶段 B）：完全镜像，路径替换为 `src/claudeteam/runtime/...` 与 `src/claudeteam/cli_adapters/...`。

### 9.2 验收

- mock 回归 8/8 PASS
- ClaudeTeam 灰度 30min 期间 0 次"误投给 busy agent"日志
- /team 渲染 < 4s
- inject_when_idle 单 agent 注入成功率 ≥ 阶段前
- restructure 同步后 `tests/static_public_contract_check.py` 仍 PASS（busy_markers 签名未破）

---

## 10. 不做（保留给后续）

- 删 busy_markers 入参 / 删 adapter busy_markers 方法 → 等 pane-diff 跑稳一周再说
- 把 quick_idle_hint 提升为唯一 idle 判定 → 同上
- 用 watch_pane / tmux 内置事件流替代轮询 → 性能优化候选，不在本轮
- per-CLI 的 SAMPLE_INTERVAL_MS 差异化配置 → 当前一个常量够用

---

*architect · 30 分钟出稿 · 2026-04-25*
*下一跳：coder 按 §9 清单做 ClaudeTeam 改动，灰度 30min 通过后同步 restructure。qa_smoke 按 §6.4 真机回归。*
