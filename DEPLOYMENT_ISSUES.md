# ClaudeTeam 部署遇到的问题记录

> 记录时间：2026-04-13
> 部署场景：Docker 部署 + 共享宿主机 lark-cli profile（bind-mount `~/.lark-cli`）
> 团队：`maintain`（manager + triager + coder + writer）

---

## 🔴 Blocker 1：Router 收不到任何事件，用户发消息无人响应

### 症状
- 容器起来后 tmux / 群聊都正常，但用户在飞书群发消息，agents 完全无感
- Router 窗口打印：`🚨 Router 启动 45 秒内未收到任何事件!`
- WebSocket 连接成功（`Connected. Waiting for events...`），但服务端永远不推消息

### 根因
共享宿主机的 Feishu App `cli_a9518e4e1d39dbc0` 是早期用 `config init --app-id ... --app-secret-stdin` 手动建的，**只写了凭证，没往服务端推 `im.message.receive_v1` 事件订阅**。这是 README Phase 1 Step 2.5 里警告过的已知坑：
> router starts fine, `chat-search --as bot` returns `ok: true`, but the router's "45 seconds 0 events" warning fires and messages from your group never reach any agent.

官方给的唯一修法是让用户手动去 Feishu 开发者控制台加事件订阅 + 重新发布，或者跑 `config init --new` 扫码推订阅。两条路都需要用户打开浏览器操作。

### 我的临时修复
给 `scripts/feishu_router.py` 加了一个**后台轮询线程**，每 5 秒调用一次 `_catchup_from_history(chat_id)`，相当于把依赖 WebSocket 的推送模式退化成 HTTP 轮询。复用了已有的 `cursor + seen_ids` 去重机制，未来 WebSocket 真的恢复了也不会重复路由。

Patch 片段（在 `main()` 里，替换原本的一次性 catchup）：

```python
def _poll_catchup_loop():
    while True:
        try:
            _catchup_from_history(chat_id)
            _refresh_heartbeat()  # watchdog 靠 health_file mtime 判活，必须刷新
        except Exception as e:
            print(f"  ⚠️ 轮询 catchup 异常: {e}")
        time.sleep(5)
threading.Thread(target=_poll_catchup_loop, daemon=True).start()
```

### 建议的正式修复
1. **setup.py 里加事件订阅存在性探测**：`config init --app-id` 路径完成后，强制跑一次 README 里那个"5 秒 probe"测试，如果 0 事件直接报错让用户去加订阅，而不是等部署完才发现
2. **把轮询模式做成正式的 fallback**：当 router 启动后 45s 没收到事件时自动切轮询（现在的警告日志建议用户手动修 App，但不是每个用户都有权限进 App 控制台）
3. **Router 应该主动订阅事件**：启动时调一次 Feishu 的 `subscribe_list` API 确认 `im.message.receive_v1` 存在，不存在直接 fail fast + 告诉用户怎么修

---

## 🔴 Blocker 2：docker-entrypoint.sh 在 bind-mount 模式下强行覆盖 profile 名

### 症状
```
🔑 lark-cli profile: maintain
⚠️ 创建 Bitable: profile "maintain" not found
   available profiles: cli_a9518e4e1d39dbc0
❌ 创建 Bitable 失败
```

### 根因
`scripts/docker-entrypoint.sh` 的 init 模式里：
```bash
PROFILE_NAME=$(python3 -c "import json; print(json.load(open('team.json')).get('session','default'))")
export LARK_CLI_PROFILE="$PROFILE_NAME"
```
**无条件**把 `LARK_CLI_PROFILE` 设成 `team.json` 的 session 名（比如 `maintain`）。

这在 inline 模式（`.env` 填了 `FEISHU_APP_ID/SECRET`）是对的——因为 entrypoint 前面刚刚用这个名字生成了新的 `config.json`。
但 bind-mount 模式（复用宿主机 `~/.lark-cli`）下，宿主机上的 profile 名是 App ID（`cli_a9518e4e1d39dbc0`），根本没有 `maintain` 这个 profile，于是 setup.py 一调 API 就炸。

### 我的临时修复
`scripts/docker-entrypoint.sh` 改成只在 inline 模式下 override：
```bash
if [ -n "$FEISHU_APP_ID" ] && [ -n "$FEISHU_APP_SECRET" ]; then
  PROFILE_NAME=$(python3 -c "import json; print(json.load(open('team.json')).get('session','default'))")
  export LARK_CLI_PROFILE="$PROFILE_NAME"
fi
```

### 建议的正式修复
这个 patch 直接上游就行，逻辑很小。顺便在 README 的 Docker 部署章节里补一段"bind-mount 模式怎么跑"——目前文档大力推 inline 模式，bind-mount 只在 `docker-compose.yml` 注释里一笔带过，一旦用户选了后者就全是坑。

---

## 🔴 Blocker 3：单文件 bind-mount + Write 工具 = 容器看不到宿主机修改

### 症状
宿主机上改完 `team.json`（加了 triager/coder/writer 三个成员），在容器里跑 `python3 scripts/hire_agent.py setup-feishu triager`，报错：
```
❌ triager 不在 team.json 中，请先添加
```
但 `cat team.json` 在宿主机和容器里看到的内容完全不同——宿主机是新版，容器还是旧版。

### 根因
`docker-compose.yml` 里对 `team.json` 用的是**单文件 bind mount**：
```yaml
- ./team.json:/app/team.json
```
Docker 单文件 bind mount 绑定的是**具体 inode**，不是路径。大多数编辑工具（VSCode 保存、Claude Code 的 Write 工具等）是"写临时文件 + rename"模式，rename 会生成新 inode，宿主机路径指向新 inode，但**容器的 bind mount 还绑在旧 inode 上**（旧 inode 成了 orphan 文件但还活着）。

复现：`stat -c %i team.json` 在宿主机和容器里看到的 inode 不一样就中招了。

### 我的临时修复
1. 临时：用 `open(path, "r+"); truncate(0); write(...)` 原地截断写法保留 inode
2. 更彻底：`docker compose down && up -d` 强制重新挂载

### 建议的正式修复
改 `docker-compose.yml`，把 `team.json` 改成挂**父目录**（或者就跟 `scripts/` / `agents/` 一样挂整个项目根的某个子目录）：
```yaml
# 旧（踩坑）
- ./team.json:/app/team.json
# 新（稳）
- ./:/app:rw  # 或者单独建一个 config/ 子目录只挂它
```
不过这样镜像里 COPY 的基线版本会被完全覆盖，需要重新评估 Dockerfile 的 COPY 策略。
或者至少在 README 里加一个醒目警告："不要用任何会 rename 的工具改 team.json，只能 `sed -i` 或者手动 `>` 原地覆盖"。

---

## 🟡 Rough Edge 1：tmux 初始化消息的竞态 bug

### 症状
容器启动后，`manager` 和 `triager` 正常进入角色并处理初始消息，但 `coder` / `writer` 的 tmux 窗口里卡在 Claude Code 的欢迎界面，初始化消息（"你是团队的 coder...请读取 identity.md..."）原封不动地躺在输入框里**没有被提交**。

### 根因
`scripts/docker-entrypoint.sh` 用的是傻瓜式 send-keys：
```bash
tmux new-window -t "$SESSION" -n "$agent" -c "$ROOT"
tmux send-keys -t "$SESSION:$agent" "IS_SANDBOX=1 claude --dangerously-skip-permissions --name $agent" Enter
sleep 2
# ...
tmux send-keys -t "$SESSION:$agent" "$INIT_MSG" Enter
sleep 1
```
问题是 Claude Code 启动到"可接收用户输入"的状态不是恒定 2 秒——第一次冷启动可能要 5-10 秒才渡过欢迎屏、trust 提示、模型加载。2 秒的 sleep 完全赌运气，triager 赌赢了，coder/writer 输了。

项目里其实已经有 `scripts/tmux_utils.py` 的 `inject_when_idle` 函数专门处理这个（在 `hire_agent.py` 里有用），但 `docker-entrypoint.sh` 的批量启动路径压根没调它。

### 我的临时修复
手动给 coder/writer 各补了一个 Enter：
```bash
docker compose exec -T team tmux send-keys -t maintain:coder Enter
docker compose exec -T team tmux send-keys -t maintain:writer Enter
```

### 建议的正式修复
把 `docker-entrypoint.sh` 的初始化消息发送改成调 `tmux_utils.inject_when_idle`，和 `/hire` 走同一条路径。

---

## 🟡 Rough Edge 2：Writer agent 处理消息时会卡在"征求确认"提示上

### 症状
Writer 多次在处理 manager 派发的任务时停下来问："请问是否要我立即回复这两条?"。不是 Claude Code 的 trust 提示，是 agent 自己在输出里主动征求确认。其他 agent（triager / coder）没有这个问题。

### 根因
推测是 `agents/writer/identity.md` 的语气/职责描述里没有明确"无需征求确认，直接执行"的规则，再加上 writer 处理的是文档任务，Claude 模型天然更谨慎（"改用户可见文案前要确认"）。

### 建议的正式修复
在所有 `agents/<name>/identity.md` 模板里统一加一条：
```markdown
## 执行规则
- 收到 manager 或 triager 派发的任务，直接执行，不征求确认
- 只有在任务超出你的职责边界、或涉及破坏性操作时才停下来问
```

---

## 🟡 Rough Edge 3：`/hire` skill 假设 tmux 在宿主机

### 症状
在 Docker 部署里，`/hire` skill 的 Step 6（`python3 scripts/hire_agent.py start-tmux <agent>`）会尝试在宿主机跑 `tmux new-window`，但 tmux session 在容器里，直接失败。

### 我的临时修复
手动把 skill 的 7 个步骤拆开执行：文件操作（team.json / identity.md / core_memory.md / 目录结构）在宿主机做，容器相关操作（`setup-feishu` / `start-tmux` / `say`）通过 `docker compose exec -T team ...` 走进去。

另外发现：因为容器的 `start` 入口脚本会读 `team.json` 里所有 agents 并批量起窗口，**如果在容器运行时改了 `team.json` 然后重启容器**，新 agent 的 tmux 窗口会自动被创建——不需要显式 `start-tmux`。这绕开了 `/hire` 的 Step 6，但不是每个用户都会这么用。

### 建议的正式修复
1. `hire_agent.py start-tmux` 加一个"检测是否在 Docker 里跑"的分支：如果宿主机找不到 session，自动走 `docker compose exec` 路径
2. 或者在 `/hire` skill 文档里加一段"Docker 部署用户跳过 Step 6，改跑 `docker compose restart` 让 entrypoint 重新拉起所有 tmux 窗口"
3. 更根本的：让 router 或 watchdog 监听 `team.json` 变化，动态增减 tmux 窗口，彻底不需要用户手动跑 `start-tmux`

---

## 🟡 Rough Edge 4：setup.py 的 shared-profile 冲突在 Docker 路径下没文档

### 症状
`setup.py` 检测到同机其他部署用了同一个 profile 时会报错 + 退出：
```
⚠️ 检测到 lark-cli profile 冲突
继续使用共享 profile (依赖 chat_id 过滤):
  CLAUDE_TEAM_ACCEPT_SHARED_PROFILE=1 python3 scripts/setup.py
```
但 Docker 路径下用户跑的是 `docker compose run --rm team init`，不是直接跑 `setup.py`——提示里的那条命令没法直接照抄。

### 我的临时修复
```bash
docker compose run --rm -e CLAUDE_TEAM_ACCEPT_SHARED_PROFILE=1 team init
```

### 建议的正式修复
错误提示里针对 Docker 部署加一行："如果你在 Docker 部署里跑，用 `docker compose run --rm -e CLAUDE_TEAM_ACCEPT_SHARED_PROFILE=1 team init`"。

---

## 🟡 Rough Edge 5：Router 45 秒警告的诊断信息对 bind-mount 模式不准

### 症状
Router 的 45 秒警告硬编码建议：
```
最可能的根因: App 未订阅 im.message.receive_v1 事件
修复方法:
  npx @larksuite/cli config init --new
  ↳ 扫码 → 选「使用已有应用」→ 选当前 App ID
```
但在 bind-mount 模式下，App 是所有团队共享的——用户很可能已经在其他团队上成功跑过，压根没权限 / 不想动 App 设置。这条建议就误导了。

### 建议的正式修复
警告里并列列出多种可能根因：
- App 未订阅事件（`config init --app-id` 路径常见）
- 同 profile 被其他 router 抢占事件流（共享 profile 场景）
- App 有订阅但事件类型不对
并给出针对性诊断命令（比如跑一次 Feishu 的 `subscribe_list` API）。

---

## 🟡 Rough Edge 6：kanban_sync.py 无故崩溃

### 症状
部署过程中看到 watchdog 通知：`🔴 [watchdog] kanban_sync.py 已崩溃并自动重启`。发生过至少一次，watchdog 自动拉起后又恢复正常。

### 根因
未定位。没时间进去挖，但既然 watchdog 能自动恢复，短期不影响使用。

### 建议的正式修复
看 watchdog 日志 + 进 tmux 窗口 `Ctrl+B s → kanban` 看崩溃时的 python traceback，修根因。也可以考虑给 `kanban_sync.py daemon` 加个自己的重试循环，异常就 log 一下 continue，不要依赖 watchdog 的进程级重启（重启代价更大，也会噪 manager 一条告警）。

---

## 📊 总结表

| 编号 | 类别     | 问题                                | 修复成本 | 上游 PR 价值 |
|------|----------|-------------------------------------|----------|--------------|
| B1   | Blocker  | Router 收不到事件（事件订阅缺失）   | 中       | 🔥 高        |
| B2   | Blocker  | entrypoint 强行 override profile    | 低       | 🔥 高        |
| B3   | Blocker  | 单文件 bind-mount inode 断链        | 低       | 🔥 高        |
| R1   | Rough    | tmux 初始化消息竞态                 | 低       | 🔥 高        |
| R2   | Rough    | writer agent 反复征求确认           | 低       | 中           |
| R3   | Rough    | /hire skill 假设 tmux 在宿主机      | 中       | 中           |
| R4   | Rough    | shared-profile 冲突文档缺失         | 低       | 低           |
| R5   | Rough    | 45s 警告诊断不准                    | 低       | 中           |
| R6   | Rough    | kanban_sync.py 偶发崩溃             | ?        | 中           |

---

## 💡 下一步建议

**如果我们要真的维护这个项目**，建议优先级：

1. **先修 B1/B2/B3**——这三个是"全新用户按 README 走 Docker 路径必踩"的 Blocker，卡掉开箱即用体验
2. **再修 R1**——初始化竞态是隐患，现在没爆是运气，agents 多了一定会爆
3. **R3 值得做成大改**——让 `/hire` 原生支持 Docker 部署（autodetect 运行环境），而不是让用户二选一
4. **R2/R5 是快速赢**，改一两行文档/identity 模板就能交付
5. **R6 需要复现才能修**，先在 watchdog 日志里加 stderr 捕获

想先从哪个开始？或者你有其他优先级？

---

# 2026-04-15 补遗：lazy-wake-v2 首次实盘部署记录

> 场景：`maintain` 容器，`feat/lazy-wake-v2` 分支代码已写完 58/58 测试 GREEN，第一次在生产容器上启用
> 启用方式：修改 `docker-entrypoint.sh` 增加 `supervisor_ticker` tmux 窗口跑 `while sleep N; do bash scripts/supervisor_tick.sh; done`，然后 `docker restart maintain`
> 结果：lazy-mode 主流程跑通（manager + supervisor 2 个 claude，其他 9 个业务 agent 💤 占位），但发现一个真 bug

## 🔴 Bug B4：`feishu_msg.py _notify_agent_tmux` 缺少 wake-before-inject

### 症状
`maintain` 重启后进入 lazy-mode。manager 启动后处理 router replay 的老消息，决定给 writer 派 AI 短剧调研任务，调了：

```bash
python3 scripts/feishu_msg.py send writer manager "<任务描述>" 高
```

命令返回成功（Bitable 收件箱写入 OK），但 writer 窗口里出现这样的乱码：

```
💤 待 wake  (agent=writer, model=opus, lazy-mode)
   router 收到业务消息后会唤醒本窗口
root@c19bfb2ddfbc:/app# g.py inbox writer:/app# ager 的新消息。请执行: python3 scripts/feishu_msg
-bash: ager: command not found
-bash: g.py: command not found
```

### 根因
`feishu_msg.py` 的 `cmd_send()` 在写完 Bitable 后调 `_notify_agent_tmux()`，后者直接 `inject_when_idle → tmux send-keys` 目标窗口。这套假设窗口里是 claude REPL，没想到在 lazy-mode 下业务 agent 窗口里是 **bash prompt**。tmux send-keys 的通知文本（"你有来自 manager 的新消息。请执行: python3 scripts/feishu_msg.py inbox writer"）被 bash 按行拆成了若干"命令"执行：

1. `你有来自` → bash 找不到命令
2. `请执行:` → 冒号结尾被当 no-op
3. `python3 scripts/feishu_msg.py inbox writer` → **真的跑起来**，读了 writer 的 inbox（side effect！）

更糟的是：在后续 tab 补全 / 历史回滚的交互中，某次 `IS_SANDBOX=1 claude --dangerously-skip-permissions` 也被 bash 真的 exec 了，结果 writer 非计划地被"启动"出 claude，从 💤 状态糊里糊涂变成了 wake 状态。看起来像是成功，其实路径是坏的。

这跟已经在 docker-entrypoint.sh 里写着的 "Bug 11" 评论是同一类事故：
> 如果窗口里只剩 bash,后续 init 消息会被当成 shell 命令跑,看起来"启动了" 实际全员死亡

设计规定 wake 必须走 `scripts/lib/agent_lifecycle.sh wake <agent>`（resume saved session + 正确的 model + 白名单检查），直接撞大运让 bash 执行消息文本是完全不同的语义。

### feat/lazy-wake-v2 的整合盲点
分支里 `feishu_router.py` 的 `wake_agent()` 确实调了 `wake_on_deliver()`（lazy-wake 设计 §C），路径是对的。但 agent-to-agent 投递走的是 `feishu_msg.py send`，**这条路径里完全没有 wake 调用**。设计文档只覆盖了"群聊消息 → router → agent"路径，漏了"agent → agent via bitable"路径。

### 我的修复（commit 在本次部署里打上）
给 `_notify_agent_tmux` 加了一个 wake-before-inject：

```python
def _notify_agent_tmux(to_agent, from_agent, message):
    """向目标 agent 的 tmux 窗口注入收件通知（best-effort）。

    lazy-wake-v2 适配:
      - 目标窗口若还是 💤 占位 (pane 里没有 claude 进程) → 先调 agent_lifecycle.sh wake
      - lifecycle wake 幂等: 已活则立即返回, 所以对非 lazy-mode 也安全
    """
    try:
        import subprocess as _sp
        lifecycle = os.path.join(os.path.dirname(__file__), "lib",
                                 "agent_lifecycle.sh")
        if os.path.exists(lifecycle):
            try:
                _sp.run(["bash", lifecycle, "wake", to_agent],
                        capture_output=True, timeout=25, check=False)
            except Exception:
                pass

        notify_text = (
            f"你有来自 {from_agent} 的新消息。"
            f"请执行: python3 scripts/feishu_msg.py inbox {to_agent}"
        )
        inject_when_idle(TMUX_SESSION, to_agent, notify_text, wait_secs=5)
    except Exception:
        pass
```

关键决策：**无条件调 lifecycle wake**，不先检测睡没睡。`agent_lifecycle.sh wake` 自己是幂等的（第一件事就是 `_lifecycle_pids_for_agent` 扫 comm=claude 的子进程，非空直接 `ℹ️ wake_agent: <agent> 已活,跳过`），所以对非 lazy-mode、对 wake 过的 agent 都是 no-op。把 "是否该 wake" 的判断收口到单一真相源（lifecycle），不让 feishu_msg.py 另开一个状态机。

### 验证
修完后在容器里跑：

```bash
docker exec maintain python3 /app/scripts/feishu_msg.py send coder manager "【lazy-wake 验收测试】..." 高
```

观察 coder 窗口：
1. `agent_lifecycle.sh wake coder` 被调 → 无 saved session 时走冷启动 `IS_SANDBOX=1 claude ... --name coder`
2. claude UI 起来
3. tmux send-keys 注入的 notify 文本在 claude REPL 里被当 user prompt 接收
4. coder 读 inbox → 看到任务 → 执行 `feishu_msg.py say coder "💻 coder ready (wake test passed)"`
5. 标记消息已读，Worked for 1m 11s 后回到 idle

完整路径跑通。

## 🟡 Bug R7：`supervisor_tick.sh` 没有外部调度器（已在本次部署一并补齐）

### 症状
lazy-wake v2 依赖 supervisor 周期性扫 agent 证据做 SUSPEND/KEEP 判断。`supervisor_tick.sh` 文件在，但容器里没 cron / 没 systemd timer / 没 nohup 循环，所以它**从来没被触发过**。整个决策引擎处于 "代码就绪但没人叫它" 的状态。

### 根因
lazy-wake v2 原设计是 "由宿主 cron / docker-compose / systemd timer 驱动"（supervisor_tick.sh 注释里写了），但 docker-entrypoint.sh 没写到谁来装这个调度器。Dockerfile 也没装 cron 包。部署文档和代码之间的 handoff 漏了。

### 我的修复
`docker-entrypoint.sh` 末尾在 watchdog 后面新开一个 tmux 窗口 `supervisor_ticker`，跑 while-sleep：

```bash
if [ "$LAZY_MODE" = "on" ]; then
  tmux new-window -t "$SESSION" -n "supervisor_ticker" -c "$ROOT"
  tmux send-keys -t "$SESSION:supervisor_ticker" \
    "while sleep \${CLAUDETEAM_SUPERVISOR_INTERVAL:-900}; do echo \"[\$(date '+%F %T')] ⏰ tick start\"; bash scripts/supervisor_tick.sh || echo \"[\$(date '+%F %T')] ⚠️  tick exit=\$?\"; done" \
    Enter
fi
```

为什么不用 cron：容器用的是 debian-slim，没装 cron；装 cron 要加 Dockerfile 一层；cron 里的 env/cwd/tmux socket 还得专门处理。tmux 窗口简单直接，attach 进来能实时看 tick 输出，watchdog 监控也好挂。

## 📋 本次部署清单（其他无 bug 但必须说明的步骤）

| 步骤 | 文件 | 说明 |
|---|---|---|
| 1 | `team.json` | 新增 `supervisor` agent，role=监工，emoji=🛡️，color=yellow。白名单里必须有 supervisor 才能做决策 |
| 2 | `agents/supervisor/identity.md` + `core_memory.md` | 新建，写明决策员的安全底线（默认 KEEP / 白名单跳过 / suspend 严格顺序 / overrides 优先级）|
| 3 | `agents/supervisor/workspace/{decisions,incidents}/` | 决策落盘目录 |
| 4 | `scripts/docker-entrypoint.sh` | 加 supervisor_ticker 窗口，lazy-mode=off 时跳过 |
| 5 | `scripts/feishu_msg.py` | 修 Bug B4：_notify_agent_tmux 加 wake-before-inject |

`team.json` 和 `agents/supervisor/` 是 gitignore 掉的（运行时生成），所以本地部署侧的改动不进 commit，只有 scripts/*.sh / scripts/*.py 进 `feat/lazy-wake-v2` 分支。

## 🎯 本次验收结果

- ✅ lazy-mode 启动后只拉起 **2 个 claude**（manager + supervisor），其他 9 个 💤 占位
- ✅ 内存 574 MB → 启动瞬间 608 MB（claude 冷启动峰值）→ 稳定态预计 200-300 MB（受 manager 补做老任务影响暂时偏高）
- ✅ `agent_lifecycle.sh wake` 幂等正确
- ✅ 修 B4 后 `feishu_msg.py send` 对 💤 agent 的投递跑通（coder 验收样例已在群里回 "coder ready"）
- ⚠️ supervisor_tick.sh 的首轮调度还没到（`CLAUDETEAM_SUPERVISOR_INTERVAL` 默认 900s），第一次 tick 要等 15 分钟才出结果
- ❓ 老板真实群聊消息 → router → manager → 全员回应 的完整 E2E 还没跑（需要有一个\"非 maintain bot\"的身份往群里发消息，暂无现成路径）

