# 凭证持久化方案（架构 spec）· 2026-04-25

**作者**：architect
**触发**：lazy-wake 修复轮（2026-04-25 上午）的 token_guard 只发了哨兵报警，没做自愈；老板 6h 后容器仍 401 死亡。本轮补"host 刷新 → 容器自动同步"闭环。
**范围**：仅 restructure 容器侧（不动 ClaudeTeam host 项目）。
**前置依据**：
- `docs/architect_lazywake_fix_plan_2026-04-25.md` §1（P0 OAuth token guard 设计）
- `docs/coder_lazywake_impl_2026-04-25.md`（guard/preflight/apply 已落盘可复用）
- 容器 prod-hardened 现状（见 §1.1）

---

## 0. TL;DR

老板的 prod-hardened 容器 **不挂 host 的 `~/.claude`**，挂的是 `${CLAUDETEAM_RUNTIME_ROOT}/creds/claude`。host 跑 `claude /login` 刷的是 `~/.claude/.credentials.json`，**runtime/creds/claude/ 永远落后**，容器 6h 必死。

**修法（三层）**：

1. **主路径**：在 prod-hardened 里**额外**只读 bind-mount host 的 `~/.claude/.credentials.json` + `~/.claude.json` 到容器一个 stage 路径（例如 `/host/claude/`），再让 token_guard 周期性比较 host stage 和容器 `~/.claude/` 的 mtime：host 更新 → 复制覆盖 → chown claudeteam:claudeteam → 重启 401 worker。
2. **备选路径**：如果 bind-mount 不可行（host umask、selinux、跨主机部署等），token_guard 检测 expired → 写一个"求救文件"到 `${RUNTIME_ROOT}/state/creds_request`，host 侧由一个简单的 systemd timer 或 cron 每分钟扫这个文件 → 检测到求救则 `cp ~/.claude/.credentials.json ${RUNTIME_ROOT}/creds/claude/` + 删除求救文件（或 host 跑 `docker cp`）。
3. **灾备路径**：guard 如何都自愈不了 → manager inbox 高优告警保留（lazy-wake 修复轮已落盘的逻辑不动）。

**核心权衡**：bind-mount 是 read-only（host→容器单向），杜绝容器把 host 凭证写坏的可能；自愈用"复制+chown"模式而非"直接挂载"，回避 uid namespace 冲突（host admin uid=1000 vs 容器 claudeteam uid=999）。

---

## 1. 现状分析

### 1.1 三个 compose 文件的差异（决定 spec 落点）

| compose 文件                                     | claude 凭证挂载                                                                                       | 用户       | 老板用？     |
|--------------------------------------------------|-------------------------------------------------------------------------------------------------------|------------|--------------|
| `docker-compose.yml`（dev）                      | `~/.claude/.credentials.json:/home/claudeteam/.claude/.credentials.json`<br>`~/.claude.json:/home/claudeteam/.claude.json` | `0:0`(root) | 否           |
| `docker-compose.live-smoke.override.yml`         | 继承 dev 的挂载                                                                                       | root       | 否（冒烟用） |
| `docker-compose.prod-hardened.yml` ✨            | `${RUNTIME_ROOT}/creds/claude:/home/claudeteam/.claude:rw`<br>`${RUNTIME_ROOT}/creds/claude/.claude.json:/home/claudeteam/.claude.json:rw` | `root`     | **✅ 是**   |

**问题就在 prod-hardened**：runtime/creds/claude 是一份**独立副本**，host `~/.claude/` 跟它没有任何同步关系。dev compose 没事是因为它直挂 host。

### 1.2 容器内 uid

| 实体                                | uid             | 备注                                                  |
|-------------------------------------|-----------------|-------------------------------------------------------|
| host admin                          | 1000            | 拥有 host `~/.claude/`                                |
| 容器 `claudeteam` 用户               | 999             | Dockerfile `useradd -r` 建的                          |
| 容器 `node`（npm 装的工具)           | 1000            | bind-mount 进来的 host 文件**就 owner=node**          |

**结论**：直接 bind-mount host 的 `.credentials.json` 进容器后，容器里 owner 是 node:node 600（mode 600 让 claudeteam 读不了），必须 root 在 entrypoint 里 `chown claudeteam:claudeteam` 后 claude 才能读。

### 1.3 lazy-wake 修复轮已交付的、本轮可复用的件

- `scripts/claude_token_guard.sh`（30min 循环）
- `scripts/preflight_claude_auth.sh`（exit 0/2/3）
- `scripts/docker-entrypoint.sh`（已加 preflight + nohup guard）
- `/app/state/claude_token_status.json`（status/expires_at/minutes_left/last_check/note）

**本轮新增的核心逻辑加在 token_guard 里**，不另起一个守护进程。

---

## 2. 接口 / 落点

### 2.1 compose 改动（核心）

`docker-compose.prod-hardened.yml` 的 `volumes:` 段**追加两行**只读 stage（不替换现有 mount）：

```yaml
# host 侧 OAuth 源（只读，guard 用来比 mtime + 复制）
- ~/.claude/.credentials.json:/host/claude/.credentials.json:ro
- ~/.claude.json:/host/claude/.claude.json:ro
```

**为什么不直接把现有 `:rw` 改成挂 `~/.claude`？**
- prod-hardened 想保留 "运行时副本" 模式（`${RUNTIME_ROOT}/creds/claude`），便于多容器/多账号部署、便于 host 凭证不直接暴露到容器写入。
- `:ro` 只读保证容器**不可能**改坏 host 凭证（防御 §6.1 风险）。
- "stage 路径 + guard 复制" 比 "直接挂载源文件" 多了一层显式的同步窗口，便于运维观察、灰度回滚。

### 2.2 token_guard 行为扩展（接口契约）

旧（lazy-wake 修复轮）：每 30 分钟扫 1 次 `~/.claude/.credentials.json` → 写 status json + 告警。

新增**复制阶段**（时序见下）：

```text
每 30 分钟（或被动触发，见 §3.2）:
  1. 读 host stage 路径 /host/claude/.credentials.json 的 mtime（host_mtime）
  2. 读容器实际生效路径 /home/claudeteam/.claude/.credentials.json 的 mtime（container_mtime）
  3. 如果 host_mtime > container_mtime + drift_secs（默认 30s）:
     a. cp /host/claude/.credentials.json /home/claudeteam/.claude/.credentials.json
     b. cp /host/claude/.claude.json /home/claudeteam/.claude.json
     c. chown claudeteam:claudeteam 两份目标
     d. chmod 600
     e. 写 /app/state/creds_sync.json 记录这次同步（from_mtime/to_mtime/result/duration）
     f. 跑一次 preflight 验证 OAuth 已恢复
     g. 如果 preflight ok → 调 wake_agent 401 死掉的 worker（见 §2.3）
     h. 如果 preflight 仍 fail → manager inbox 告警 "creds 同步成功但 preflight 仍失败"（rare）
  4. 没更新 → 走旧逻辑（写 status + 告警）
```

### 2.3 401 worker 重新拉起

guard 同步完凭证后，并不重启容器，只对**已经 401 死的 claude 进程**触发 wake：

```bash
# 在 token_guard 内：
for agent in $(get_lazy_workers); do
  if ! _lifecycle_pids_for_agent "$agent" >/dev/null; then
    wake_agent "$agent"
  fi
done
```

`get_lazy_workers` 简单实现：从 `team.json` 读所有 `cli_type=claude_code` 的 worker，再加 supervisor。**只动 claude 系列**，因为 OAuth 401 只影响它们。

### 2.4 entrypoint 改动

`scripts/docker-entrypoint.sh`：在已有的"watchdog 后 nohup guard"位置之前**先做一次同步**（首启不依赖 guard 的 30min 间隔）：

```bash
# 容器一上来先把 host stage 的最新 creds 拉进来
if [ -f /host/claude/.credentials.json ] && [ -f /home/claudeteam/.claude/.credentials.json ]; then
  if [ /host/claude/.credentials.json -nt /home/claudeteam/.claude/.credentials.json ]; then
    cp /host/claude/.credentials.json /home/claudeteam/.claude/.credentials.json
    cp /host/claude/.claude.json /home/claudeteam/.claude.json
    chown claudeteam:claudeteam /home/claudeteam/.claude/.credentials.json /home/claudeteam/.claude.json
    chmod 600 /home/claudeteam/.claude/.credentials.json /home/claudeteam/.claude.json
    echo "[entrypoint] creds bootstrap: pulled fresh from /host/claude"
  fi
fi
```

**preflight 失败行为**（§4 详述）：preflight 退 2/3 时，entrypoint 已有逻辑（lazy-wake 修复轮）会拉红横幅 + 只起 manager 窗口，不动；本轮**只是把"同步"前置一步**，让 preflight 第一次跑就有最新凭证。

---

## 3. 备选 / 兜底

### 3.1 备选路径 A · bind-mount 不可行时

可能的不可行情况：
- selinux 强制类型：mount 路径权限标签冲突
- 跨主机部署（容器和凭证不在同一台机）
- host 路径 `~/.claude` 包含大量子目录被禁挂（cache/, projects/ 等）

**做法**：guard 在容器内**主动求救**：

```bash
# token_guard 检测到 expired 且未通过 stage 拿到新凭证时：
echo "$(date -Iseconds) expired need_refresh" > ${RUNTIME_ROOT}/state/creds_request
# host 侧由 systemd timer / cron / 一个 shell-only daemon 每分钟扫这个文件:
#   cp ~/.claude/.credentials.json ${RUNTIME_ROOT}/creds/claude/
#   cp ~/.claude.json ${RUNTIME_ROOT}/creds/claude/
#   rm ${RUNTIME_ROOT}/state/creds_request
```

state 目录已是 bind-mount（见 prod-hardened 第 52 行）所以容器写 host 读毫无障碍。

**本 spec 不强制 host 侧 systemd timer 落盘**：先实现主路径（§2.1-§2.4），如灰度发现真有 bind-mount 阻塞才补。

### 3.2 兜底路径 B · 主动触发同步

除了 30min 轮询，guard 暴露一个 USR1 信号或 socket（简单做：扫 `/app/state/creds_sync_request` 文件存在则立即跑一轮同步）。

用法：
- manager 在容器外手动 `touch /home/admin/runtime/.../state/creds_sync_request`
- 或 supervisor 决策决定 "now 立刻同步" 时同样 touch
- guard 下一轮（30s 检查间隔）发现 → 立即同步 + 删除标记文件

### 3.3 灾备路径 C · 切 ANTHROPIC_API_KEY

沿用 lazy-wake 修复轮 §1 已经写过的灾备：`.env` 填 `ANTHROPIC_API_KEY=sk-ant-...` → 重建容器，guard 检测 env 非空自动早退（已实现）。

本轮**不重复落盘**这一段，只在 TROUBLESHOOTING 里 link 过去。

---

## 4. 阈值 / 边界

### 4.1 guard 检测周期

| 参数                              | 默认  | 说明                                          | env 覆盖                            |
|-----------------------------------|-------|-----------------------------------------------|-------------------------------------|
| `GUARD_INTERVAL_SECS`             | 1800  | 主循环间隔（30min，沿用上一轮）              | `CLAUDETEAM_TOKEN_GUARD_INTERVAL`   |
| `STAGE_CHECK_INTERVAL_SECS`       | 30    | 同步阶段轮询间隔（高频）                       | `CLAUDETEAM_CREDS_SYNC_INTERVAL`    |
| `MTIME_DRIFT_SECS`                | 30    | host_mtime > container_mtime + drift 才同步  | `CLAUDETEAM_CREDS_MTIME_DRIFT`      |
| `WARN_BEFORE_EXPIRY_MIN`          | 60    | 距离过期 N min 内告警                         | `CLAUDETEAM_TOKEN_WARN_MIN`         |

为什么 stage check 30s 而 main guard 30min？
- 主循环要做：mtime 检查 + 同步 + chown + preflight + wake → 较重，30s 一轮负担轻但浪费
- 把"轻量探测 mtime"和"重逻辑"分两层：每 30s 探一下 host mtime，发现变化才进重逻辑
- 实现：guard 一个进程内套循环，外 1800s 兜底（含告警），内 30s 走轻探测

### 4.2 自愈触发条件

guard 进入 sync 重逻辑当且仅当：

1. host stage 路径存在（`/host/claude/.credentials.json` is_file）
2. 且 host_mtime > container_mtime + MTIME_DRIFT_SECS
3. 且容器 token 当前 status ∈ {expired, warning} **OR** host 比容器新且 token 未到期（提前同步也无害）

逻辑表：

| host newer? | container expired? | 动作                                            |
|-------------|--------------------|-------------------------------------------------|
| ❌          | ❌                 | 静默                                            |
| ❌          | ✅                 | 走旧告警逻辑（host 也过期了，要 manager 介入） |
| ✅          | ❌                 | sync（提前同步，无副作用）                      |
| ✅          | ✅                 | sync + wake 401 worker                          |

### 4.3 自愈失败时

任一步骤失败 → manager inbox 高优告警，文案模板：

```text
🚨 凭证同步失败
phase: <bootstrap/sync_copy/chown/preflight_post_sync>
host_mtime: 2026-04-25 13:45 CST
container_mtime: 2026-04-25 07:34 CST
last_error: <stderr 简化版>
建议：host 侧手动 docker cp + 重启容器
```

不重试（避免 inbox 刷屏）；下一个 30min 主循环会再试一次。

### 4.4 边界情况

| 情况                                                   | 处理                                                                                  |
|--------------------------------------------------------|---------------------------------------------------------------------------------------|
| host stage 文件不存在（compose volume 没挂上）          | 静默走旧 guard 逻辑；首次 startup 由 entrypoint 检测并 manager 告警                  |
| host stage 文件存在但内容空 / 损坏                      | preflight 会 exit 3；不触发 sync；告警一次                                           |
| 同步过程中 host 侧又写了一次（race）                    | cp 是 atomic（原子 rename） — 用 `cp host stage tmp && mv tmp dest` 模式             |
| 同步成功但 worker 已 wake（pid 还在）                   | guard 不重 wake；只 wake "pid 不在" 的 lazy worker                                    |
| `.claude.json` 缺失但 `.credentials.json` 在            | 同步 `.credentials.json` 即可（claude.json 包含 onboarding state，不影响 OAuth）     |
| host_mtime 异常老（admin 不小心 touch -t 过去时间）     | drift 加上"host_mtime 不能比 now 老于 90 天"硬限；超期当不存在                       |
| 容器 chown 失败（权限不够）                             | prod-hardened 是 root 用户应不会发生；触发时按 §4.3 告警                             |

---

## 5. entrypoint preflight 失败行为（沿用 lazy-wake 已落盘的红横幅，本轮只补"同步先行"）

时序：

```text
docker compose up -d
  ↓
entrypoint 启动:
  ↓
[NEW §2.4] 同步 host stage → 容器 ~/.claude/（如果 host newer）
  ↓
preflight_claude_auth.sh
  ├── exit 0 → 拉所有正常 tmux 窗口 + nohup guard + ticker 循环
  ├── exit 2 (warning) → 拉所有窗口 + 红色橙色横幅 + 立即 manager inbox 告警
  └── exit 3 (expired) → 只拉 manager 窗口 + 大红横幅 + manager inbox 高优告警
                          （worker 窗口不拉，避免 401 死亡循环）
```

红横幅由 entrypoint 在 manager pane 上 `tmux send-keys` 发送，文案：

```
█████████████████████████████████████████████████████████████
🚨 OAuth 凭证已过期 / 即将过期 — 容器以"管控模式"启动
原因：preflight exit=<2|3>
请 host 侧执行: claude /login
然后等待 ≤60 秒（guard 会自动同步）
█████████████████████████████████████████████████████████████
```

横幅文本 / 颜色由现有 entrypoint 已实现的能力提供（lazy-wake 修复轮），本轮只**确保它在同步动作之后**触发，避免横幅误报。

---

## 6. 回归点 / 灰度策略

### 6.1 单元/脚本级回归（coder 自测）

| 测点                                                         | 期望                                                                |
|--------------------------------------------------------------|---------------------------------------------------------------------|
| host stage 文件比容器新 → guard sync 一次                    | `/app/state/creds_sync.json` 写入；container `.credentials.json` mtime 跟齐 |
| host 等于容器（mtime 同） → guard 不动                        | 无新 sync 记录                                                       |
| host 老于容器 → guard 不动 + 不告警                          | 无                                                                   |
| host 缺失 → guard 走旧逻辑                                   | status_json 仍写出                                                   |
| 模拟 host /login 后 mtime 跳 → guard 30s 内 sync             | sync_json `result=ok`                                                |
| 同步成功后 401 worker → guard wake_agent 调用                | `agent_lifecycle.sh` 日志可见                                        |

`scripts/regression_creds_sync.sh`（新增 mock 脚本，纯 shell + jq）跑通这 6 项即过。

### 6.2 真机灰度（qa_smoke 跑）

**前置**：当前生产容器 OAuth 已过期（lazy-wake 报告里 `status=expired`）。这本身就是天然的灰度入场点。

| # | 步骤                                                                                          | 期望                                                                              |
|---|-----------------------------------------------------------------------------------------------|-----------------------------------------------------------------------------------|
| 1 | host 跑 `claude /login` 走 device flow → `~/.claude/.credentials.json` mtime 刷新             | host 侧 ok                                                                        |
| 2 | 容器内 `cat /host/claude/.credentials.json | head -c 50` 应能读到（验证 :ro 挂载生效）        | 文件可读                                                                          |
| 3 | 等 ≤ 60s（guard 30s 间隔 + 30s buffer）                                                       | `/app/state/creds_sync.json` 出现 `result=ok`；status_json `status=ok`            |
| 4 | 401 死掉的 worker_cc / worker_codex（codex 用的也是 ChatGPT login）pane 再 hello              | 不再 401，回出正常文本                                                            |
| 5 | supervisor_ticker 下一轮 tick                                                                 | supervisor 醒来后能正常出 decisions（先决条件由 P0 解锁）                         |
| 6 | manager inbox 没有"creds 同步失败"告警                                                        | inbox 干净                                                                        |

### 6.3 不影响主链路其他容器（保险）

- 改动**只**在 prod-hardened compose 文件，不动 dev / live-smoke
- ClaudeTeam（host 跑 team01）**完全不动**（任务约束已明示）
- 改动后回滚：把 prod-hardened compose 里新增的两行 `- ~/.claude/...:/host/claude/...:ro` 注释掉 + `docker compose up -d` 即可。token_guard 检测到 host stage 缺失自动降级到只告警模式，不会崩。

### 6.4 灰度阶段划分

| 阶段 | 内容                                            | 时长     | 通过条件                                    |
|------|-------------------------------------------------|----------|---------------------------------------------|
| A    | coder 落盘 §6.1 mock 回归                      | 30min    | 6 测点全过                                  |
| B    | 改 compose + docker compose up -d 重建容器     | 5min     | 容器起来后 preflight 0；纯启动行为正常      |
| C    | host /login 触发 → 真机灰度（§6.2）             | 5min     | 6 步骤全过                                  |
| D    | 观察 6 小时（一个完整 token 周期）              | 6 hours  | 中途 token 自动续期同步 → 无 401 / 无告警   |

D 失败 → 立即回滚（§6.3 注释 + up -d），ROI 评估后下一轮再做。

---

## 7. 风险与防御

### 7.1 host 凭证被容器写坏（核心风险）

**防御**：bind-mount 用 **`:ro`**（只读），容器物理上无法写 host stage 路径。所有写操作在容器自己的 `/home/claudeteam/.claude/` 内，host 完全隔离。

进一步：
- entrypoint 和 guard 的 cp 一律是 `host_stage → container_home` 单向
- 任何场景都**不**做 `container_home → host_stage` 反向同步
- 文档（TROUBLESHOOTING）明示这一约定

### 7.2 多容器/多机部署的并发刷新

**场景**：将来 host 上跑 N 个容器都挂同一份 `~/.claude/.credentials.json:ro`。
- 只读所以无写竞争
- N 个容器都会复制到自己的 `/home/claudeteam/.claude/.credentials.json` → N 份独立副本，互不影响
- host /login 一次刷新所有 N 个容器（在 30s + drift 内同步）

**结论**：天然支持 N=∞。

### 7.3 chown 误操作把 host 文件改主

prod-hardened 容器是 root；ro 挂载下 chown 会失败（EROFS），但脚本如果忘了加 `|| true` 会让 entrypoint 退出。
**对策**：所有 chown 命令在 entrypoint/guard 中加保护：
- 只对 container_home 路径 chown
- 调用前显式 `if [[ "$dest" =~ ^/home/claudeteam/\.claude ]]; then chown ...; fi`
- 路径白名单守卫，避免笔误 chown 到 `/host/claude/...`

### 7.4 race：host 正在写 + 容器读到一半

`~/.claude/.credentials.json` 的写是 atomic（OAuth refresh 走 tmp + rename），所以 ro 读永远能读到一致快照。容器侧 cp 也用 `cp host_stage tmp && mv tmp dest` 维持原子性。

**结论**：无 race。

### 7.5 host stage 路径泄露敏感信息到非授权进程

`/host/claude/.credentials.json` 在容器里 mode 默认随 host（600，owner=node:node = uid 1000）。容器内只有 root 能读（root 总能读）。其它 uid（claudeteam=999）读不了 stage，但这没关系——它们读自己 home 下的副本。

**对策**：spec 不要求把 stage 改成 644；保持 600 → 限制为容器 root 可见，最安全。

### 7.6 token_guard crash → 凭证永远不同步

guard 是 nohup 起的单进程；crash 后进程消失。
**防御**：watchdog（已存在）扫 guard pid 是否在；crash → 重启。
本轮 watchdog 扫描列表加上 `claude_token_guard.sh`（或者 entrypoint 落地一个 systemd-style 检查）。

### 7.7 风险汇总表

| 风险                                          | 影响    | 防御                                       |
|-----------------------------------------------|---------|--------------------------------------------|
| host 文件被写坏                                | 高      | `:ro` 单向                                 |
| chown 误操作 host                              | 高      | 路径白名单，且 ro 挂载会 EROFS 立即报错    |
| guard crash 导致久不同步                       | 中      | watchdog 扫 guard pid                      |
| host 缺失或权限拒绝                            | 中      | 自动降级到旧告警逻辑 + manager inbox       |
| 多容器并发                                     | 低      | ro + 各自独立 home 副本                    |
| race 读不一致                                  | 低      | atomic rename；cp 双段                     |
| 横幅误报（同步前 preflight 已跑）              | 低      | entrypoint 把同步前置                      |
| `.claude.json` 不同步导致 onboarding 状态丢   | 低      | 一并同步                                   |

---

## 8. 给 coder 的清单

### 8.1 改动文件（restructure 仓内）

| # | 文件                                          | 类型     | 改动                                                                                                        |
|---|-----------------------------------------------|----------|-------------------------------------------------------------------------------------------------------------|
| 1 | `docker-compose.prod-hardened.yml`            | 修改     | volumes 段追加 2 行 `:ro` stage 挂载                                                                        |
| 2 | `scripts/claude_token_guard.sh`               | 修改     | 主循环加内层 30s STAGE 检查；mtime 比较；cp + chown 600 + 写 sync_json + wake 死 worker                     |
| 3 | `scripts/docker-entrypoint.sh`                | 修改     | preflight 之前先做一次 stage→home 同步（同步代码可抽到 `lib/creds_sync.sh` 给 guard 复用）                  |
| 4 | `scripts/lib/creds_sync.sh`（新建）            | 新增     | 函数 `sync_creds_from_host()`；包 cp/atomic/chown/mode/路径白名单；返回 0/非0；输出结构化 log 给 sync_json |
| 5 | `scripts/regression_creds_sync.sh`（新建）    | 新增     | mock 6 测点 → exit 0/非0                                                                                    |
| 6 | `docs/TROUBLESHOOTING.md`                     | 修改     | 新增"OAuth 凭证同步"节：host /login 流程；同步失败排查；灾备切 API key 链接                                |
| 7 | `scripts/watchdog.py`（如存在该文件）          | 修改     | 监控列表加 `claude_token_guard.sh` 进程                                                                    |
| 8 | `agents/architect/workspace/reports/`         | 不动     | 本 spec 已交付                                                                                              |

### 8.2 验收（qa_smoke 接 §6.2）

灰度 D 阶段（6 小时）通过 = 验收 PASS。中途任意红线触发即回滚。

### 8.3 不做（明示边界）

- ❌ 不动 `docker-compose.yml` / `live-smoke.override.yml`（不影响 dev / smoke）
- ❌ 不动 ClaudeTeam（host 项目）
- ❌ 不在容器里实现 OAuth refresh 逻辑（device flow 需浏览器，不可行）
- ❌ 不做反向同步（容器→host）
- ❌ 不引入新依赖包（只用 bash + 已有 jq + cp + chown）

---

## 9. 不在代码范围（manager / 运维侧）

1. host 侧 `claude /login` device flow（浏览器）— 这是凭证刷新的唯一来源，代码不能替代
2. （备选 §3.1 落地时）host 侧 systemd timer / cron 配置 — 当主 bind-mount 路径不可行时
3. host 侧 `~/.claude/` 权限维持（默认 600 即可）

---

## 10. 后续路线（不在本轮）

- token guard + creds sync 跑稳一周后，把 lazy-wake 修复轮里的"哨兵告警"降级（30min 间隔放宽到 2h），降低 inbox 噪音
- 探索 `~/.claude.json` 里的 `oauthRefreshToken` 是否能在容器内直接刷 access_token 不依赖 host 浏览器（实验性，不在本轮）
- 把 `:ro` 挂载从单文件升级到目录（`~/.claude/:/host/claude/:ro`），让 sessions / projects 也只读暴露给容器（非凭证用途，例如让容器读 host 的 commands/skills）— 评估后再决定

---

*architect · 30 分钟出稿 · 2026-04-25*
*下一跳：老板过目 → coder 按 §8.1 改 5 文件 + 灰度（§6.4）→ toolsmith review → qa_smoke §6.2 真机验收。*
