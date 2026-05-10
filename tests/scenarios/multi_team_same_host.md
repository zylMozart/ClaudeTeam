# Two ClaudeTeam deployments on one host (multi-team-same-container)

## 目的

证明同一台机器（容器或 host）上能并行跑两套 ClaudeTeam，互不串扰：
团队 B 收到的飞书事件不会泄到团队 A 的 manager pane，团队 A 的
`claudeteam say` 不会发到团队 B 的群，OAuth/凭证/HOME/状态都隔离。

`docs/DEPLOYMENT.md` 的 *Multi-team isolation* 节给出了 env-var 套路；
本剧本是它的端到端验收。

## 适用范围

- 类型：local-only + 真飞书（两个独立 Feishu App + 两个独立 chat_id）
- 凭证：每团队一份独立的 FEISHU_APP_ID/SECRET（**永远从文件读，不在
  shell history 或日志里复读字符串**）
- 操作员：boss / 任一开发者
- 不依赖 Docker 多容器；目的就是**同进程空间共存**

## 前置条件

```text
/data/teams/
  team_a/
    claudeteam.toml      # session = "TeamA"  ; chat_id = "oc_aaa..."
    state/               # 已有团队 A 的活动状态
  team_b/
    claudeteam.toml      # session = "TeamB"  ; chat_id = "oc_bbb..."
    state/               # 团队 B 的状态目录（首次为空）

/data/secrets/team_a/credentials.env  # FEISHU_APP_ID / FEISHU_APP_SECRET (mode 0600)
/data/secrets/team_b/credentials.env  # 另一个 App 的凭证
```

每个 `claudeteam.toml` 的 `[team]` 段里必须写不同 `session` 值（默认
`ClaudeTeam` → tmux session 名碰撞），并配不同 `chat_id`。两个 App
属于同一企业内的不同自建应用，各自被拉进各自的群。

> ⚠️ 不要让两套部署共用同一个 Feishu App / 同一个 chat —— 那是 host_smoke §8
> 的 subscribe-lock + 事件随机切片场景，是另一类问题。

## 操作

### A. 先把团队 A 起好（已有部署照常）

```bash
# 终端 1
set -a; . /data/secrets/team_a/credentials.env; set +a
export CLAUDETEAM_STATE_DIR=/data/teams/team_a/state
export CLAUDETEAM_CONFIG_FILE=/data/teams/team_a/claudeteam.toml
export LARK_CLI_PROFILE=teamA
unset CLAUDETEAM_AGENT_HOME_BASE   # 让团队 A 走 /data/agent-home/ 默认（向后兼容）
cd /data/teams/team_a
claudeteam up
claudeteam health      # 全绿
```

### B. 在第二个终端起团队 B（绝不复用 A 的环境变量）

```bash
# 终端 2 —— 全新 shell，先把任何 A 的 env 都清掉再 source B 的
unset CLAUDETEAM_STATE_DIR CLAUDETEAM_CONFIG_FILE CLAUDETEAM_TEAM_FILE \
      CLAUDETEAM_RUNTIME_CONFIG LARK_CLI_PROFILE \
      FEISHU_APP_ID FEISHU_APP_SECRET \
      LARKSUITE_CLI_APP_ID LARKSUITE_CLI_APP_SECRET \
      LARKSUITE_CLI_TENANT_ACCESS_TOKEN

set -a; . /data/secrets/team_b/credentials.env; set +a
export CLAUDETEAM_STATE_DIR=/data/teams/team_b/state
export CLAUDETEAM_CONFIG_FILE=/data/teams/team_b/claudeteam.toml
export CLAUDETEAM_AGENT_HOME_BASE=/data/agent-home-b   # ← 关键：避免 /data/agent-home 与 A 撞
export LARK_CLI_PROFILE=teamB
cd /data/teams/team_b
claudeteam up
claudeteam health      # 全绿
```

> ⚠️ **不要 echo `$FEISHU_APP_SECRET` 或 `cat /data/secrets/team_*/credentials.env`**
> 来"调试"。验证凭证生效请用 `claudeteam health`（看 lark-cli profile + 状态）
> 或 `printenv | grep -c FEISHU_APP_ID`（数变量在不在，不打印值）。

## 期望

### 1. 进程层可见性（操作员肉眼可分辨）

```bash
ps -ef | grep -E 'claudeteam (router|watchdog)' | grep -v grep
# → 应有 4 行：team_a/router、team_a/watchdog、team_b/router、team_b/watchdog
#   每行的 cwd（/proc/<pid>/cwd）分别落在 /data/teams/team_a/ 和 /data/teams/team_b/

tmux ls
# → 至少两个 session：TeamA 和 TeamB（名字取自各自 toml 的 [team].session）
```

### 2. 状态目录不重叠

```bash
ls /data/teams/team_a/state/   # router.pid / watchdog.pid / facts/ / lark_tenant_token.json
ls /data/teams/team_b/state/   # 同样的文件名，互不重叠

# Tenant token 缓存在各自 state 下，不再走 /tmp 共享
test -f /data/teams/team_a/state/lark_tenant_token.json
test -f /data/teams/team_b/state/lark_tenant_token.json
test ! -f /tmp/claudeteam_tenant_token.json   # 老路径不再使用
```

### 3. Agent HOME 不重叠（claude OAuth 不打架）

```bash
ls /data/agent-home/    # 团队 A 的 manager / worker_* 各自的 ~/.claude.json
ls /data/agent-home-b/  # 团队 B 的同名 agent；互不读写对方的 .credentials.json
```

### 4. 飞书事件分发**只**到对应团队

操作步骤：
1. 在团队 A 的群里 `@manager 现在几点了`
2. 在团队 B 的群里 `@manager 同样的话`

期望：

```bash
tmux capture-pane -t TeamA:manager -p -S -30 | grep '现在几点'
# → 团队 A manager 看到这条消息

tmux capture-pane -t TeamB:manager -p -S -30 | grep '现在几点'
# → 团队 B manager 也看到，但两次的 chat_id / 时间戳不同

# 互不串扰检查：A 的 router 日志不应有 B 的 chat_id
grep -c "$(awk -F'"' '/^chat_id/{print $2}' /data/teams/team_b/claudeteam.toml)" \
        /data/teams/team_a/state/router.log
# → 0（团队 A 的 router 从不处理 B 的 chat 事件）
```

### 5. `claudeteam say` 不跨群

在团队 A 的环境下：

```bash
# 终端 1
claudeteam say worker_cc "from team A [marker-A-$(date +%s)]" --to user
```

期望团队 A 的群里出现这条卡片，团队 B 的群里**不出现**任何含
`marker-A-` 的内容。

### 6. 老团队默认行为不变

新装一台机器（或在干净 state 里）只走团队 A 的 §A 步骤：

- 不设 `CLAUDETEAM_AGENT_HOME_BASE` → agent HOME 仍解析为 `/data/agent-home/<agent>`
- 不设 `CLAUDETEAM_CONFIG_FILE` → 仍取 cwd 下的 `claudeteam.toml`
- Tenant token 缓存路径变成 `<state_dir>/lark_tenant_token.json`（不再是 `/tmp/`）；
  对单团队部署是路径换地方，行为不变（缓存仍生效，token 仍能复用）

## 失败排查

| 现象 | 诊断 |
|------|------|
| 团队 B 的事件被 A 的 manager 看到 | A、B 的 `chat_id` 配错了同一个 → 检查两份 toml |
| 团队 B 的 `claudeteam say` 报 `chat_id not set` | 当前 shell 漏 `export CLAUDETEAM_CONFIG_FILE` 或 cwd 不在 team_b/ |
| 团队 B 的 manager pane HTTP 400 "Bot/User can NOT be out of the chat" | App B 没被拉进 chat B 的群；用 lark-cli 把 bot 加进去 |
| 两套 daemon 都跑，但 ps 看 `cwd` 都指 team_a | B 终端启动时忘了 `cd /data/teams/team_b` 之前先 unset 变量 |
| `claudeteam health` for B 显示 "tenant_token cache: /tmp/..." | 你跑的是老版本（< 2026-05-09 该剧本生效之前），升级 |
| 团队 B 的 `~/.claude.json` 被团队 A 的 reidentify 改写 | `CLAUDETEAM_AGENT_HOME_BASE` 没设；同名 agent 共享了 /data/agent-home/<agent> |

## 不在范围

- **同一 Feishu App 双订阅**：那是 lark-cli subscribe-lock 场景，看 host_smoke §8。
- **跨容器隔离**：本剧本聚焦同进程空间；Docker 多容器隔离是 docker-compose
  的本职工作，不需要本仓库代码层面再做什么。
- **团队 A 在跑时，把 A 的 agent home 也搬到 `agent-home-a/`**：可以做，
  但属于"重命名既有部署"，不是双团队隔离的硬要求；先别动 A，向后兼容路径
  仍是 `/data/agent-home/`。
