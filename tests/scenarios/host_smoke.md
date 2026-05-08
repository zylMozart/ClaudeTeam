# macOS 本机部署冒烟测试 — 一分钟版

## 目的

刚部署完，想用一分钟过一遍证明能用。覆盖：

- 部署上线（venv 激活、`claudeteam up`、`claudeteam health`）
- 用户 OAuth（设备授权流程，只跑一次终身有效）
- 9 条斜杠命令全覆盖
- 普通文本路由（验证 R174「manager 是唯一接口」契约）
- worker 反向路由（worker 卡片自动转回 manager 收件箱）

不覆盖：容器部署（看 [docker_deploy.md](docker_deploy.md)）、Round C 真任务协作（看 [round_c_real_task.md](round_c_real_task.md)）。

## 适用范围

- 平台：macOS（Apple Silicon 或 Intel）。Linux 主机大部分通用，但 keychain 部分要换成文件路径
- 已装：Python 3.10+（macOS 上推荐 `/opt/homebrew/bin/python3.14`）、tmux、node + npx、`claude` 或 `codex` 在 PATH 中
- 已建：飞书自建 App，开放平台后台开了 `im:message` 权限并启用了 `im.message.receive_v1` 长连接事件订阅
- 机器人已加入目标群，群的 `chat_id` 已知

## 0. 前置环境变量（每次新开终端都要设）

```bash
cd /path/to/ClaudeTeam
source .venv/bin/activate
export CLAUDETEAM_STATE_DIR="$PWD/state"
export LARK_CLI_NO_PROXY=1
export CLAUDETEAM_LARK_SEND_AS=bot
export PYTHONUNBUFFERED=1
```

## 1. 团队上线（前置环境检查，非通过点）

这一节不算冒烟通过条件——只是确保后续步骤的环境是好的。

```bash
claudeteam up        # 起 tmux 会话 + router + watchdog
claudeteam health    # 三个 agent ✅，router 与 watchdog 都活着
```

环境检查应得：health 输出全绿（最多容忍 `lark_profile blank` 一条 ⚠️，不致命）。
**真冒烟从 §3 开始**——前面只是"环境是不是搭好"。

**环境失败排查**：

- "claude: not found"——CLI 适配器找不到二进制，检查 `$PATH`
- "pane up but CLI not ready"——常见原因是 codex 弹更新提示。`tmux capture-pane -t ClaudeTeam:worker_codex -p` 看一眼，按 `3 Enter` 选「Skip until next version」即可

## 2. 用户 OAuth（一次性）

如果 `lark-cli auth list` 显示「No logged-in users」，跑一次：

```bash
LARK_CLI_NO_PROXY=1 lark-cli auth login --domain im --recommend --no-wait --json
```

输出里的 `verification_url` 就是浏览器要打开的地址，登录飞书账号点「授权」。
然后用返回的 `device_code` 完成：

```bash
LARK_CLI_NO_PROXY=1 lark-cli auth login --device-code <从上一步拷贝>
```

授权成功后 token 写进 macOS keychain（service `lark-cli-credentials`，账号是你的 open_id），永久有效（自动续期）。

之后冒烟就可以用 `--as user` 模拟你自己发消息：

```bash
LARK_CLI_NO_PROXY=1 lark-cli im +messages-send \
  --chat-id <你的 chat_id> --text "/team" --as user
```

## 3. 斜杠命令矩阵（9 条 + 1 条边界用例）

每条都用 `--as user` 触发，等 router 接收 → 看群里有没有期望的卡。

```bash
CHAT="oc_xxxxx"   # 你部署用的 chat_id
SEND() { LARK_CLI_NO_PROXY=1 lark-cli im +messages-send --chat-id "$CHAT" --text "$1" --as user --format json | python3 -c 'import json,sys;d=json.load(sys.stdin);print(d.get("data",{}).get("message_id",d))'; }

SEND "/help"                  # 期望 🆘 命令清单卡
SEND "/team"                  # 期望 👥 三 agent 状态卡
SEND "/health"                # 期望 🩺 服务器 CPU/内存/磁盘卡
SEND "/usage"                 # 期望 📊 用量卡（约 3-5 秒，慢）
SEND "/tmux"                  # 期望 📺 manager 默认 10 行
SEND "/tmux worker_cc"        # 期望 📺 worker_cc 10 行
SEND "/tmux worker_codex 25"  # 期望 📺 worker_codex 25 行
SEND "/tmux foobar"           # 期望「⚠️ 未知 agent」
SEND "/foo"                   # 期望「⚠️ 未知斜杠命令，建议 /help」
```

**通过条件（看群里）**：每条都在 10 秒内能在群里看到对应的卡片，标题
能跟下面表对上：

| 发的 | 群里期望卡的标题前缀 |
|---|---|
| `/help` | 🆘 ClaudeTeam 自定义斜杠命令 |
| `/team` | 👥 /team — 员工实时状态 |
| `/health` | 🩺 /health — 服务器负载 |
| `/usage` | 📊 /usage |
| `/tmux` | 📺 /tmux manager — 最近 10 行 |
| `/tmux worker_cc` | 📺 /tmux worker_cc — 最近 10 行 |
| `/tmux worker_codex 25` | 📺 /tmux worker_codex — 最近 25 行 |
| `/tmux foobar` | 群里直接出现纯文本「⚠️ 未知 agent」 |
| `/foo` | 群里直接出现纯文本「⚠️ 未知斜杠命令，建议 /help」 |

拉历史核对：

```bash
LARK_CLI_NO_PROXY=1 lark-cli im +chat-messages-list --chat-id "$CHAT" \
  --as bot --page-size 12 --format json | python3 -c "
import json,sys
for m in json.load(sys.stdin)['data']['messages'][:12]:
    print(f\"[{m['create_time']}] {m.get('msg_type'):11} {m.get('content','')[:90]}\")"
```

**失败排查**（**只在群里没看到预期卡时才看**）：

- 某条没回——看 `state/router.log`（提交 `c0996a5` 之后才有），定位是不是 `[slash]` 入口之后 `[send_card] result=None`
- 卡片标题对不上——看 [slash_matrix.md](slash_matrix.md) 的失败标准表

### 状态变更类（按需）

下面 4 条会真改 worker 状态，只在你愿意承担副作用时跑：

```bash
SEND "/send worker_cc smoke ping"   # 注入 pane；worker_cc LLM 会简短 ack（"收到 / pong / ready"）
SEND "/compact worker_cc"           # ⚠️ 已知不稳：claude 2.x 把自动注入的 /compact 当文本，
                                    # LLM 回 "can't be triggered from inside a response"。建议
                                    # 用 /clear 替代清上下文。
SEND "/clear worker_cc"             # 清历史 + 重新注入 identity（rehire 等价）
SEND "/stop worker_cc"              # 送 C-c 中断当前动作（不杀 pane；slash 自身 help 写的是"中断"）
```

## 4. 普通文本路由（验证 R174）

证明 router 把 4 条人话都只投给 manager 的收件箱。manager 之后**可以**主动
派单给 worker（「你在吗」之类的简单问，他可能懒得自己 echo 而 dispatch 给
worker_cc 直接答；这是 manager 的判断力，不算契约破）。R174 真正禁止的是
**router 跳过 manager、把 worker 当独立接口直接送投递**——这条用 inbox 文件
查最严谨。

```bash
SEND "你好"                              # 无 @ 无前缀
SEND "@worker_cc 你在吗"                 # 显式 @worker_cc
SEND "@team 全员同步进度"                # 广播触发词
SEND "全体注意：smoke ping $(date +%s)"  # 中文广播 + 时间戳锚定
```

**通过条件**：

1. 群里能看到 manager 的回复卡（manager 配色蓝色），第 4 条带时间戳——证明
   manager 真处理了具体消息，不是回复以前的指令；不超过 60 秒
2. `state/facts/inbox.json` 里这 4 条的 `to` 字段**全部是 manager**，没有
   `to=worker_cc` / `to=worker_codex` 的人话条目（manager dispatch 后产生的
   `from=manager, to=worker_cc` 是合法派单，不计在 R174 之内）

```bash
# 严谨验证：直接看 inbox 投递记录
python3 -c "
import json
msgs = json.load(open('state/facts/inbox.json'))['messages']
for m in msgs[-12:]:
    print(f\"to={m['to']:13} from={m['from']:10}  {(m.get('text') or '')[:70]}\")"
# 期望最近 4 条人话 to= manager, from= user。
# 任何 to=worker_*, from=user 的条目就是 R174 破了。
```

**失败排查**（仅当 inbox 里出现 `to=worker_*, from=user` 才看）：

- 查 `feishu/router.classify_event` 有没有被回退到老的 @-mention 路由
- 群里 manager 没回——manager pane 卡住或没在工作。先 `tmux capture-pane -t ClaudeTeam:manager -p | tail -30` 看 LLM 状态；再看 `state/router.log` 看路由是不是 ROUTE 到 manager

## 5. Worker → manager 反向路由（R174 的例外分支）

证明 worker 自己发的卡能被 manager 看到并在群里**继续动作**——闭环就这一条。

```bash
ANCHOR="smoke-反向-$(date +%s)"
claudeteam say worker_cc "$ANCHOR" --card
```

**通过条件（看群里）**：

1. 60 秒内群里能看到 worker_cc 的卡（蓝/绿色 worker 配色），内容含 `$ANCHOR`
2. 之后 60 秒内**还能看到 manager 的另一张卡**——证明 manager 看到了
   worker 的话并做出了反应（可能是简短 ack、可能是询问、可能是无视后仍发了
   状态汇总）。**关键：必须有 manager 的卡，不只是 worker 的**
3. manager 的卡内容里能找到 worker_cc 名字或 `$ANCHOR`，证明它真在 react
   这条而不是别的事

**失败排查**：

- 只看到 worker_cc 卡，半分钟后没 manager 卡——R174 的 worker→manager
  反向分支没生效，或 manager 卡住了。先 `claudeteam inbox manager` 看消息
  有没有进来。如果没进来，看 `feishu/router._card_sender_agent`。如果进
  来了 manager 没动，看 manager pane（identity init 是否完成）

## 6. 路由器重启不丢消息

证明 router 死掉再起来时，期间发的消息**最终在群里有 manager 的回应**。

```bash
WATCHDOG_PID=$(cat state/watchdog.pid)
kill -STOP $WATCHDOG_PID    # 暂停 watchdog 防止立刻 respawn
ROUTER_PID=$(cat state/router.pid)
kill $ROUTER_PID
sleep 2

# router 不在期间发两条，**带时间戳锚定**
T1="$(date +%s)"
SEND "回放测试 A $T1"
SEND "回放测试 B $T1"
sleep 3

# 重启 router + 恢复 watchdog
kill -CONT $WATCHDOG_PID
claudeteam up
```

**通过条件（看群里）**：

router 重启后 90 秒内，群里能看到 manager 的回复卡，**内容里包含
`$T1` 这个时间戳**——证明这两条停机期间发的消息确实进了 manager 并被
react 了一次（manager 处理两条还是一条不强求，但至少要看到对应时间戳的
回应）。

**失败排查**：

- 群里没有任何带 `$T1` 的 manager 卡——catchup 没正确补回来。看
  `state/router.log` 找 `📥 catching up`，如果数字 != 2 说明 cursor
  没正确推进；如果有 `catchup fetch failed` 说明 catchup 调用失败
  （在提交 `780fd08` 之前，bot-only 部署会因为 `--as user` 默认报权限错）

## 7. 懒启动 worker（lazy）

某些 worker 配 `"lazy": true` 时，`claudeteam start` 不真起 CLI；首条
进收件箱的消息才触发起 CLI。**通过条件：群里能看到这个 lazy worker 在
被点名后真发出报到卡**——验证从 placeholder 到活 CLI 再到群里说话的
完整链路。

```bash
# 给 worker_codex 临时打 lazy
python3 -c '
import json
t = json.load(open("team.json"))
t["agents"]["worker_codex"]["lazy"] = True
json.dump(t, open("team.json","w"), ensure_ascii=False, indent=2)
'
claudeteam down && claudeteam up

# 在群里点名让 worker_codex 报到（带锚定）
ANCHOR="lazy-wake-$(date +%s)"
SEND "@manager 让 worker_codex 现在报个到，回复里带上 $ANCHOR"
```

**通过条件（看群里）**：

3 分钟内群里出现 worker_codex 的卡，**内容里能看到 `$ANCHOR`**——
证明 manager 收到 → manager 派单 → worker_codex 第一次被唤醒起 CLI →
真的处理 inbox 并发卡。

**清理**：

```bash
python3 -c '
import json
t = json.load(open("team.json"))
t["agents"]["worker_codex"].pop("lazy", None)
json.dump(t, open("team.json","w"), ensure_ascii=False, indent=2)
'
claudeteam down && claudeteam up
```

## 8. 多部署冲突（同一个 App 抢订阅）

⚠️ **2026-05-06 重测发现实际行为与本节描述不符**——下文仅为说明 lark-cli 1.0.23
之后的真实情况：

**lark-cli 单实例锁是 fcntl-advisory**（`~/.lark-cli/locks/subscribe_<app_id>.lock`），
进程死后锁立即释放。**lark 服务端层面也允许多个 WebSocket 同时连接**——只是
事件会**随机分散**给已连接的多个 subscribe（每个连接收到部分事件）。

实际表现：
- 第二个 daemon 启动**不会被立刻拒绝**（除非第一个 daemon 此刻持有锁）
- 真实危害是**事件被 lark 服务端随机切片**——两个 daemon 各收到一半，
  造成 host 部署下 router 时不时 silent-stall（自己分到了 0 事件）

本节烟测**不再可靠**。改用如下规程：

1. **每个 deploy 单独跑**（host 部署 vs 容器部署互斥使用，不要同时活）
2. **本机 vs 容器同 lark App 的常见坑**：docker-compose 默认 mount
   `~/.lark-cli` 整个目录，host 与容器共享 lock 文件——互相干扰。修法：
   测前手动停另一方，或 docker-compose 改 mount 只共享 config 不共享 locks
3. **容器 + host 共用同一 chat 的部署**没有意义（事件会乱抢），生产规划时
   每个 deploy 应独立 chat

```bash
# 验证 lark-cli 锁状态
ls -la ~/.lark-cli/locks/
fuser ~/.lark-cli/locks/subscribe_<app_id>.lock
# → 拿到锁的 PID 是当前持有 subscribe 的那个进程；含容器进程在 host
#   namespace 里的真实 PID
```

**清理**：

```bash
rm -rf /tmp/test-conflict
```

## 9. `chat.publish` 过滤 + `say --to`

证明 `[chat.publish]` toml 配置真正影响群里能看到什么消息——把某通道设为
`false` 后，对应 sender→receiver 的 say 走 audit log 但**不**进群。

```bash
# 验证默认全 true：worker_cc 各 --to 目标都进群
ANCHOR="publish-default-$(date +%s)"
claudeteam say worker_cc "test→user [$ANCHOR]" --to user
claudeteam say worker_cc "test→manager [$ANCHOR]" --to manager
sleep 5
# 群里应当看到两张绿卡，都含 $ANCHOR
```

**通过条件（看群里）**：

1. 两张卡都进群（默认 publish 全 true / always）
2. 把 `claudeteam.toml` 的 `worker_to_manager = false`，重启 router：
   ```bash
   sed -i.bak 's/worker_to_manager = true/worker_to_manager = false/' claudeteam.toml
   kill $(cat state/router.pid); claudeteam up
   ANCHOR2="publish-silenced-$(date +%s)"
   claudeteam say worker_cc "test→manager silenced [$ANCHOR2]" --to manager
   claudeteam say worker_cc "test→user OK [$ANCHOR2]" --to user
   ```
3. 群里**只看到 →user 那张卡**，→manager 那条不进群（CLI 会打
   `📝 worker_cc → silenced by [chat.publish.worker_to_manager]=false; logged only`）
4. 还原：`mv claudeteam.toml.bak claudeteam.toml; kill $(cat state/router.pid); claudeteam up`

**LLM 是否真带 `--to`**（Step 4c 的 prompt 强化目标）：

```bash
ANCHOR3="publish-llm-$(date +%s)"
LARK_CLI_NO_PROXY=1 lark-cli im +messages-send --chat-id "$CHAT" \
  --text "数 src/claudeteam/feishu/ 下 .py 个数 [$ANCHOR3]" --as user
sleep 90
tmux capture-pane -t ClaudeTeam:manager -p -S -50 | grep -A1 "claudeteam say"
tmux capture-pane -t ClaudeTeam:worker_cc -p -S -50 | grep -A1 "claudeteam say"
```

**通过条件**：两个 pane 的 `claudeteam say ...` 命令行尾都有 `--to user`
或 `--to manager`，不是裸 `claudeteam say <agent> "..."`。如果发现裸 say，
identity prompt 没生效；rerun `claudeteam reidentify --all` 后再试，
仍然不带就是 prompt 工程需要再加强。

## 10. 收尾

冒烟通过则不需清理；如果想回到干净状态：

```bash
claudeteam down
```

只停 pane 和守护进程，不会删收件箱、日志、游标。要彻底清空：`claudeteam reset`（看 [_archive/team_down_and_reset.md](_archive/team_down_and_reset.md)）。

## 已知的本机特有怪现象

1. **`/usage` 的 Claude Code 段会显示「读取失败」**——macOS 上 claude OAuth 存在 keychain，不在文件里。ccusage 找不到 `~/.claude/.credentials.json`。Codex 与 Kimi 段正常
2. **重新部署后 worker pane 可能「Not logged in」**——claude 续期 token 时只更新 keychain，每个 agent home 下的 `state/agent-home/<agent>/.claude/.credentials.json` 是当时快照，过几天会过期。临时解：`claudeteam down && claudeteam up`，让 lifecycle 从 keychain 重新物化一遍
3. **codex 启动可能弹更新框**——挡住 ready marker 60 秒超时。手动 `tmux send-keys -t ClaudeTeam:worker_codex 3 Enter` 选 Skip-until-next，再 `claudeteam reidentify --all`
4. **第一次 user OAuth 之后，每个新 shell 仍要 `export` 那 4 个环境变量**——没持久化的话 `claudeteam say` 偶尔会走 user 身份失败

## 不在范围

- 容器部署专属问题（`FEISHU_APP_ID` / tenant_access_token 自动注入）：看 [docker_deploy.md](docker_deploy.md)
- 多份部署互相切换：看 [team_switch.md](team_switch.md)
- manager 拆任务派 worker、worker 完工汇报、manager 写 review 报告（真协作）：看 [round_c_real_task.md](round_c_real_task.md)
- agent 之间互相发信（`claudeteam send worker_a worker_b "..."`）：看 [local_message_cycle.md](local_message_cycle.md)
