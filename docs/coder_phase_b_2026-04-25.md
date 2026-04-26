# Phase B 完工报告 — pane-diff idle 容器灰度

**日期**：2026-04-25
**作者**：coder (ClaudeTeam host)
**关联 spec**：`docs/architect_pane_diff_idle_2026-04-25.md` §9.2 / §6
**前情**：Phase A (host ClaudeTeam) PASS 5/5。Phase B 把 5 处改动镜像到 restructure，热补丁进容器，跑 8min 容器灰度。

---

## 1. 改动文件（restructure repo，未 push）

| # | 路径 | 类型 | 关键改动 |
|---|------|------|---------|
| 1 | `src/claudeteam/runtime/tmux_utils.py` | 修改 | + `import hashlib, re`；+ 常量块 `SAMPLE_COUNT=10` `SAMPLE_INTERVAL_MS=300` `HASH_NORMALIZE=True` `QUICK_HINT_MAX_AGE=2.0`（env 覆盖名按 spec）；+ `_strip_control` `_normalize_pane`（剥 ANSI + 去末行空格 + `\d+` 抹数字 + 截最后行）；+ `_is_agent_idle_legacy` `_is_agent_idle_pane_diff`；`is_agent_idle` 改调度器，`CLAUDETEAM_IDLE_LEGACY=1` 走旧路径；+ `quick_idle_hint`；`inject_when_idle` 默认 `wait_secs` 5→10、`poll_interval` 0.5→0 |
| 2 | `src/claudeteam/commands/team.py` | 修改 | `for name, info in agents.items()` 改 `ThreadPoolExecutor` 并行；总耗时从 N×3s 砍到 ~3s |
| 3 | `src/claudeteam/cli_adapters/base.py` | 修改 | `busy_markers()` docstring 加「since 2026-04-25 only used by quick_idle_hint」 |
| 4 | `tests/regression_tmux_inject.py` | **新建** | 11 个 mock case：C1-C8 + C8b + C8c + legacy fallback。FakeCapture harness、env 默认 SAMPLE_COUNT=2 / INTERVAL=0 加速 |

**与 Phase A 完全镜像**，只有路径替换（`scripts/...` → `src/claudeteam/...`）。

**Spec 偏差**（Phase A 已与 manager 对齐）：`_PANE_DIGIT_RE` 用 `\d+` 而非 spec 写的 `\b\d{1,4}\b`，因 spec 正则在「1m 16s」digit 紧贴字母时不匹配 → C3 直接挂；`\d+` 通过。代价是 UUID 数字也被剥，但 UUID 帧间不变，hash 仍稳定。

---

## 2. 容器热补丁（claudeteam-restructure-team-prod-hardened-1）

```
docker cp .../tmux_utils.py        container:/app/src/claudeteam/runtime/tmux_utils.py
docker cp .../team.py              container:/app/src/claudeteam/commands/team.py
docker cp .../base.py              container:/app/src/claudeteam/cli_adapters/base.py
docker cp .../regression_tmux_inject.py container:/app/tests/regression_tmux_inject.py
```

4 个文件全部 `docker cp` 进容器 `/app/`，**无 rebuild**。

**router 局部重启**：`tmux send-keys -t restructure:router C-c`（×2）→ 重新发起 `npx @larksuite/cli --profile slash-smoke event +subscribe ... | python3 scripts/feishu_router.py --stdin`。新 PID = 346336（旧 185 已退）。pane tail 显示 "📡 模式: stdin 事件流"、"📥 历史补抓完成"，正常订阅。

---

## 3. 8min 容器灰度（4 个 gate 全过）

### 3.1 容器内 mock 回归 — 11/11 PASS

```
$ docker exec claudeteam-restructure-team-prod-hardened-1 \
    python3 /app/tests/regression_tmux_inject.py
✅ regression_tmux_inject (restructure) passed: pane-diff C1-C8 + legacy fallback
```

C1 静止/idle、C2 spinner/busy、C3 时间戳归一/idle、C4 capture 失败/busy、C5 cursor 抖动/idle、C6 流式/busy、C7 无 marker 但变化/busy、C8/C8b/C8c quick_idle_hint 三态、legacy fallback 双向 — 全过。

### 3.2 容器内 /team P95 — PASS（< 4s 门）

5 次连续 `collect_and_format()`：

```
run[1] 2.767s
run[2] 2.765s
run[3] 2.762s
run[4] 2.767s
run[5] 2.758s
---
min=2.758s p50=2.765s p95=2.767s max=2.767s
GATE PASS (P95<4s)
```

并行 ThreadPoolExecutor 把 5 worker × 3s 串行 = 15s 砍到 ~2.77s。

### 3.3 容器内 inject 落地 — PASS（≤ 10s 门）

throwaway tmux window `restructure:smoke_target`（bash），3 次 `inject_when_idle`：

```
inject[1] ok=True dt=5.047s
inject[2] ok=True dt=5.051s
inject[3] ok=True dt=5.051s
GATE PASS (<=10s)
```

主要耗时 = pane-diff 内部 ~3s + verify_submit 1s + send/Enter 间歇 1s。throwaway 测完即 `tmux kill-window` 清理。

### 3.4 容器内 legacy 回滚 — PASS（< 1s）

```
$ CLAUDETEAM_IDLE_LEGACY=1 python3 -c "from claudeteam.commands.team import collect_and_format; ..."
legacy /team: 0.019s, lines=8
GATE PASS (<1s for legacy)
```

回滚开关验证：env=1 → 单帧 busy_markers 实现（19ms），可秒级降级。

---

## 4. 边界声明

- ❌ 未 `compose down/rebuild`（按 manager 边界）
- ❌ 未 push GitHub
- ❌ 未在群里 say
- ❌ 未影响真实 worker 窗口（inject 测试用 throwaway window）
- ✅ 4 个文件全走 `docker cp` 热更新
- ✅ router 局部重启（kill + 重发命令），新 PID 346336 在线
- ✅ 4 个 gate（mock 11/11、/team P95<4s、inject ≤10s、legacy <1s）全过
- ✅ 临时调低 SAMPLE_COUNT/INTERVAL 的 env **未使用**（gate 测的就是默认 10×300ms 真实开销）；不需要 unset

---

## 5. 状态 → 待命

restructure 容器已稳定运行 pane-diff 默认配置（10×300ms ≈ 2.77s）。如需双仓 PR，等老板点头。
如灰度阶段有人观察到误投/抢注入，立即 `docker exec ... env CLAUDETEAM_IDLE_LEGACY=1` 进 router pane 重启即降级回旧逻辑。

— coder · Phase B 完工 · 2026-04-25
