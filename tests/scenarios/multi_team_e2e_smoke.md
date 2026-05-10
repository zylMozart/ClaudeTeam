# Multi-team real-chat end-to-end smoke

## 目的

证明 multi-team-same-container 的隔离 + transport 完整链路真打通：
从模拟 boss 消息进 router，到 manager pane 真在飞书群里 `say` 回去，
**全链路用真飞书 API + 真 chat + 真 bot token**——不是 mock subscribe、
不是 inject pane 跳过 router、不是 unit test。

`tests/scenarios/multi_team_same_host.md` 验的是「隔离对照不串扰」；
本剧本验的是「隔离开了之后链路真能跑通」。两者互补。

## 适用范围

- 类型：本地 + 真飞书 API（团队 B 群是新建的隔离测试群）
- 触发时机：每次动到 `lifecycle._PROPAGATED_ENV` / `feishu/lark.py` 的
  tenant token cache 路径 / `agent_home`、或者交付一份 multi-team 部署
  recipe 给操作员之前
- 操作员：开发者本人或 worker_test
- **不依赖**老板真人入群 — 用 fake-boss canary 走 `subscribe.process_lines`
  注入消息绕过外部事件订阅（候卡点 1 完工后再补真 boss 链路）

## 前置条件

```text
/data/repo/ClaudeTeam_wt_multi_team/    # feat/multi-team-same-container worktree
                                        # 至少含 91ad676 + b84e03c + 9c41279

/data/secrets/team_b/credentials.env    # FEISHU_APP_ID / FEISHU_APP_SECRET (mode 0600)
                                        # team B 应用，独立于 team A app
                                        # ⚠️ 永远从文件读，绝不复制粘贴到 shell

/data/claudeteam.toml                   # team A 现役配置，整轮验证不许动
/data/state/                            # team A 现役 state，整轮验证不许动
/data/agent-home/                       # team A 现役 OAuth 落点
```

需先确认 team A 在跑（`tmux ls` 看到原 session + `cat /data/state/router.pid`
能 `ps -p` 到活进程）。多团队隔离的核心目标就是**team A 零回归**。

## 操作

### 1) 准备 team B 配置 + env wrapper

`/data/claudeteam-b.toml` —— 与 team A toml 同 schema，但：
- `session = "AB2-EndToEnd"`（必须 ≠ team A 的 session 名）
- `chat_id = "<新建的 team B 群 oc_xxx>"`
- `[team.agents]` 精简到 manager_b + worker_b 两个

`/data/scripts/teamb-env.sh` —— source-only wrapper，建立 team B 进程组的 env：

```bash
#!/usr/bin/env bash
set -a
. /data/secrets/team_b/credentials.env   # 灌 FEISHU_APP_ID/SECRET
set +a
export LARKSUITE_CLI_APP_ID="$FEISHU_APP_ID"      # lark-cli native names
export LARKSUITE_CLI_APP_SECRET="$FEISHU_APP_SECRET"
export PYTHONPATH=/data/repo/ClaudeTeam_wt_multi_team/src
export CLAUDETEAM_STATE_DIR=/data/state-b
export CLAUDETEAM_CONFIG_FILE=/data/claudeteam-b.toml
export CLAUDETEAM_AGENT_HOME_BASE=/data/agent-home-b
```

> ⚠️ 绝不在 shell 里 `echo $FEISHU_APP_SECRET` 或 `cat credentials.env`。
> 全程靠 `set -a; . file; set +a` 的 source-then-export 模式，secret
> 字符串永远不进 shell history / process listing / log。

### 2) 创 team B 测试群（首次部署）

lark-cli 在 agent context 下默认 external-provider 模式不接受 env，
直接走原生 Feishu API：

```bash
bash -c '
set -a; . /data/secrets/team_b/credentials.env; set +a
python3 - <<PYEOF
import json, os, urllib.request
# fetch tenant_access_token
req = urllib.request.Request(
    "https://open.feishu.cn/open-apis/auth/v3/tenant_access_token/internal",
    data=json.dumps({"app_id": os.environ["FEISHU_APP_ID"],
                     "app_secret": os.environ["FEISHU_APP_SECRET"]}).encode(),
    headers={"Content-Type": "application/json"}, method="POST")
token = json.loads(urllib.request.urlopen(req, timeout=10).read())["tenant_access_token"]
# create chat
req = urllib.request.Request(
    "https://open.feishu.cn/open-apis/im/v1/chats?set_bot_manager=true",
    data=json.dumps({"chat_type": "public",
                     "name": "AB2-EndToEnd-Test",
                     "description": "team B 真群聊端到端验证容器"}).encode(),
    headers={"Content-Type": "application/json",
             "Authorization": f"Bearer {token}"}, method="POST")
print(json.loads(urllib.request.urlopen(req, timeout=10).read())["data"]["chat_id"])
PYEOF
'
```

把打印出来的 `oc_xxx` 写进 `/data/claudeteam-b.toml` 的 `chat_id`。

### 3) 启 team B 整套

```bash
mkdir -p /data/state-b /data/agent-home-b
bash -c '. /data/scripts/teamb-env.sh && python3 -m claudeteam.cli up'
```

期望输出：

```
🚀 created tmux session AB2-EndToEnd (initial window: manager_b)
  → manager_b (claude-code) spawned
  → worker_b (claude-code) spawned
✅ team AB2-EndToEnd started (2 agents)
🚀 router launched (pid <X>)
🚀 watchdog launched (pid <Y>)
✅ team up — run `claudeteam health` to verify
```

### 4) 隔离对照检查（A 完全没动）

```bash
tmux ls                                    # 期望 2 session：原 + AB2-EndToEnd
ps -p $(cat /data/state/router.pid)         # team A router 仍活
ps -p $(cat /data/state/watchdog.pid)       # team A watchdog 仍活
ps -p $(cat /data/state-b/router.pid)       # team B router 新 pid
ps -p $(cat /data/state-b/watchdog.pid)     # team B watchdog 新 pid
```

四个 pid 互不重叠，且 team A 两个 pid 与 step-3 之前 ps 的值一致。

### 5) 验 claude 进程 env 已拿到 PYTHONPATH

```bash
MGR_PID=$(tmux list-panes -t AB2-EndToEnd:manager_b -F '#{pane_pid}' | head -1)
CLAUDE_PID=$(pgrep -P $MGR_PID -f claude | head -1)
cat /proc/$CLAUDE_PID/environ | tr '\0' '\n' | grep -E '^(PYTHONPATH|FEISHU_APP_ID|CLAUDETEAM_STATE_DIR)='
```

期望：
- `PYTHONPATH=/data/repo/ClaudeTeam_wt_multi_team/src`
- `FEISHU_APP_ID=<team B app id>`（与 team A 不同）
- `CLAUDETEAM_STATE_DIR=/data/state-b`

> ⚠️ 不要去看 `pane_pid` 自己（shell pid）的 env — 那是 tmux server
> 启动时的 env，team A 起 server 时灌的就是 team A 值。真正干活的是
> claude 子进程，pane_env_prefix 通过 spawn_cmd prepend 进 claude
> 的 env，再继承给 claude shell-out 的 `claudeteam` 子进程。

### 6) 投 fake-boss canary（绕外部事件订阅）

```bash
bash -c '. /data/scripts/teamb-env.sh && python3 << PYEOF
import json, time, uuid
from claudeteam.feishu.subscribe import process_lines
ev = {
    "message_id": f"om_FAKE_{uuid.uuid4().hex[:12]}",
    "chat_id": "<team B chat_id>",
    "sender_id": "ou_FAKE_BOSS_CANARY",
    "sender_type": "user",  # ←关键：模拟人类发送，触发 ROUTE-to-default
    "content": json.dumps({"text": "@manager_b 数 team B agent 数量 say 到群"}),
    "message_type": "text",
    "create_time": str(int(time.time() * 1000)),
}
stats = process_lines(
    [json.dumps(ev)],
    team_agents=["manager_b", "worker_b"],
    chat_id="<team B chat_id>",
    bot_id="",
    default_target="manager_b",
)
print(f"handled={stats.handled} dropped={stats.dropped}")
PYEOF
'
```

期望：`handled=1 dropped=0`。

### 7) 验 manager_b 真 say 出去

等 ~60s（claude thinking + bash run），然后：

```bash
tmux capture-pane -t AB2-EndToEnd:manager_b -p -S -50 | tail -30
```

期望看到 manager_b pane 里：
1. inbox 提示行
2. `Bash(claudeteam team)` 调用 + 结果
3. `Bash(claudeteam say manager_b "..." --to user && claudeteam read ...)`
4. **`✅ manager_b → chat (message_id=om_xxx)`** ← 这条是绿灯关键

如果第 4 步是 `❌ Feishu send failed ... HTTP 400 Bot/User can NOT be
out of the chat`，说明 PYTHONPATH 没透传到 claude 进程，pane 内
shell-out 的 claudeteam 走了 system import path 撞了 /tmp 共享 token
cache（见坑点 §3）。

最终去 AB2-EndToEnd-Test 群里肉眼确认那条 say 真出现了。

### 8) 自动化 canary runner（无人值守演示）

如果想让真老板（已加群）被动观看完整链路滚动跑，用 bundled runner：

```bash
python3 tests/scenarios/multi_team_e2e_canary.py --interval 60 --max 20
# stop early: touch /tmp/multi_team_canary.stop
```

机制：runner 借 team A bot creds（从 team A 现役 watchdog 进程 env
读取，不写盘不进 history），每 `--interval` 秒往 team B 群发一条
"模拟老板"消息（`@manager_b 报道一下进度` 等业务感 phrase）。sender
是 team A app（≠ team B 自己），按 im.message.receive_v1 设计本应
触发 team B router；但因 §6 的 lark-cli ws 协议层 broken，实际靠
team B 的 catchup HTTP pull 接住（节奏由 `[router].stale_event_threshold_s`
决定，默认 600 → hotfix 后 60）。

效果：每 ≤60s 老板看到群里"假老板"提问 → ≤60s 后 manager_b 真处理
→ manager_b say 回群 — 完整链路滚动演示。

⚠️ 这是 canary 演示，不是冒烟测试 — 真冒烟跑 §6 fake event 注入
（无延迟、无外部依赖），canary 是为"让真老板看见"那一程。

## 期望

| # | 项 | 通过判据 |
|---|---|---|
| E1 | tmux 2 session 共存 | `tmux ls` 显示 team A 原 session + `AB2-EndToEnd` |
| E2 | 4 daemon pid 互不冲 | team A/B 的 router/watchdog 4 个 pid 都活 + 各自 state_dir 内 |
| E3 | claude 进程 env 完整 | PYTHONPATH + FEISHU_APP_ID(team B) + STATE_DIR=/data/state-b 都齐 |
| E4 | fake event 路由通 | `process_lines` 返回 `handled=1 dropped=0` |
| E5 | manager_b inject + thinking | `tmux capture-pane` 看到 `Bash(claudeteam say ...)` 调用 |
| E6 | manager_b say 真送达 | 输出含 `message_id=om_xxx` + 群里肉眼可见 |
| E7 | team A 零回归 | step-4 的 team A 两个 pid 与本 scenario 跑前一致 |

7 项全过 = 真群基建端到端绿灯。

## 失败排查（已踩过的坑）

### §1 lark-cli 报 `no access token available for bot`

agent context 下 lark-cli 走 external-provider 屏蔽了 `FEISHU_APP_*`
env。绕路：直接 urllib POST 到 Feishu auth endpoint 拿 tenant token，
再 export `LARKSUITE_CLI_TENANT_ACCESS_TOKEN` + `LARKSUITE_CLI_APP_ID/SECRET`
三件套；或者一次性脚本（如 step-2 的创群）完全跳 lark-cli 直走 urllib。

### §2 router subscribe 静默 0 events

team B app 后台默认没启用「事件与回调 → 长连接 → 订阅 im.message.receive_v1」。
表征：`/data/state-b/router.log` 只有启动行没有 `📥 catching up N`，
+subscribe 子进程 alive 但永远不喂数据。修复：管理员去 Feishu 开发者
后台开订阅。本 scenario 的 step-6 fake event 通道就是为绕这个外部
依赖设计的。

### §3 manager_b say 报 `Bot/User can NOT be out of the chat`

**根因**：system `/usr/local/bin/claudeteam` 是 main 4c01976 而非 #9
分支，main 分支的 `feishu/lark.py` hardcode `/tmp/claudeteam_tenant_token.json`
作为 cache 文件。team A 先 cache 了自己 token；team B pane 内 shell
out 的 claudeteam 读到 team A token，用 team A bot 去 team B 群发 →
bot 不在群 → 400。

**修复**：commit 9c41279 把 `PYTHONPATH` 加进
`lifecycle._PROPAGATED_ENV`，让 pane 内 claudeteam 走启动时透传的 #9 src
（其 lark.py 已改成 state_dir-relative cache）。

**回归验证**：跑完 step-7 期望看到 message_id 而非 400。

### §4 看 pane shell pid env 缺关键变量 ≠ 修复失效

tmux server 是 team A 启动时拉起的，所有 pane shell 继承 server 的
env（team A 状态）。pane_env_prefix 是 prepend 到 spawn_cmd 字符串
的——这是 **claude 子进程** 的 env，不是 pane shell 自己的 env。

诊断时必须用 `pgrep -P $PANE_PID -f claude` 找到 claude pid，再读
`/proc/$CLAUDE_PID/environ`。看 pane shell（bash）的 environ 会得到
**误诊"修复没生效"**。

### §5 down 报 "still alive" 但 ps 已无进程

`claudeteam down` 警告 `pid X still alive after 12s SIGTERM+SIGKILL`，
但实际 `ps -p X` 已找不到。SIGKILL 已生效，警告是 readback 时序问题，
不影响后续 up。

## 不在范围

- ❌ 真 boss user 在群里发消息（候卡点 1 飞书后台订阅完工）
- ❌ team B 用与 team A 不同的 OAuth 账号（两团队共享 host bind-mount
  的 `/root/.claude/.credentials.json`，本 scenario 不试图分账号）
- ❌ docker compose 形式部署多团队（host 本地双进程已足够覆盖）
- ❌ 多于两个团队同 host（本 scenario 只验 N=2，N≥3 留作未来）
- ❌ 性能 / 并发压力测试（功能先打通）

## 一句话范围

> 创团队 B 测试群 + 启隔离 ClaudeTeam + 投 fake-boss canary + 验
> manager_b 真 say 回群 + 全程 team A 零影响 = 多团队真群聊端到端绿灯。
