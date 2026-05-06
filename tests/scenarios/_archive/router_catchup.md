# 路由器重启时不丢消息（catchup）

## 场景

router 守护进程死掉时（被 kill / 主机重启 / OOM），lark-cli `event +subscribe`
长连接断了——这期间发到群里的消息只在飞书服务端，本地一行都没收到。
重启 router 后，旧的 `state/router.cursor` 标记着上次处理到哪一条；
router 先调 `chat-messages-list` 把 cursor 之后的所有消息拉回来，按时间
正序喂给 `subscribe.process_lines`，走相同的"路由 → 收件箱 + tmux 注入"链路。
回放完再开启 live 订阅。

每条非 DROP 的 Decision 应用完都会推进 cursor。各类 DROP（dedup / cross_team /
bot_self / empty）不推进——它们没副作用，下次启动重新看见也无害。

## 范围

- 类型：本机端到端（需要真 lark-cli + 真 chat_id + 真 OAuth profile）
- 凭证：飞书 bot 凭证就够，不需要 user OAuth。提交 `780fd08` 之后
  catchup 自动尊重 `CLAUDETEAM_LARK_SEND_AS=bot`，调用 `chat-messages-list`
  时用 bot 身份。R174 之前 catchup 默认 `--as user`，bot-only 的部署会失败

## 前置条件

- `runtime_config.json` 里有真的 `chat_id` 与 `lark_profile`
- `team.json` 至少含 manager
- `CLAUDETEAM_STATE_DIR=$PWD/state`
- `CLAUDETEAM_LARK_SEND_AS=bot`（如果是 bot-only 部署）
- 旧的 `router.cursor`（如果存在）记录了某条早于待回放消息的 timestamp
- `claudeteam start` 已经把 tmux 起好

## 操作

```bash
# 1) 起 router 收一条消息，正常处理
claudeteam router &
ROUTER_PID=$!
# 在群里发"hello A"
sleep 5
cat $CLAUDETEAM_STATE_DIR/router.cursor    # 应记到 hello A 的 message_id

# 2) 杀 router
kill $ROUTER_PID
wait $ROUTER_PID 2>/dev/null

# 3) router 不在期间，连发两条：
#    "missed B"
#    "@worker_codex check C"
#    （两条都会路由到 manager——R174 后 @worker_codex 仍只进 manager 收件箱）

# 4) 重启 router
claudeteam router &
ROUTER_PID=$!
sleep 10   # 等 catchup 跑完
```

## 期望

1. router 启动日志（提交 `c0996a5` 之后看 `state/router.log`）出现
   `📥 catching up <N> missed message(s)`，N 等于停机期间漏掉的条数
2. `claudeteam inbox manager` 拿到这两条："missed B" 和 "@worker_codex check C"
   （**两条都到 manager**，R174 后 @worker_codex 不再分流）
3. `claudeteam inbox worker_codex` 没有新行
4. manager pane 的 banner 之后能看到这两段文本被注入
5. live 订阅照常工作：再发一条新消息能即时被处理
6. `router.cursor` 已推进到最后一条处理过的 message_id（含 catchup 期间的）

## 反例

- `chat-messages-list` 失败（bot 没 im:message 权限或 OAuth 过期）：日志含
  `⚠️ catchup fetch failed`，**但 live 订阅照常启动**——不会因为 catchup 异常
  阻塞
- cursor 文件损坏：`read_cursor` 返回 `{}`，等同首次启动 → 拉回最近一页
  消息（默认 50 条）。dedup 兜底重复
- 群里没有新消息：`pending: 0`，直接进 live
- 在提交 `780fd08` 之前，bot-only 部署会因 catchup 默认 `--as user` 拿到
  `need_user_authorization` 错误。如果你看到这个，确认你的 router 是基于
  R178 之后的代码

## 已知风险

1. **catchup 期间发的消息和 live 流有 dedup 重叠窗口**——同一条 message_id
   既可能被 catchup 拉到、又可能在 router live 起来后从订阅推过来。`seen_msg_ids`
   set 去重，但前提是 set 在进程内连续。如果你测的时候 `kill -9`（不走清理路径）
   再立刻重启，理论上有几毫秒的窗口看到一条消息被走两次的迹象（不会写两次
   收件箱，但 pane 注入可能重复）
2. **R174 例外分支也走 catchup**——worker 自己发的卡，停机期间也算 missed
   message，重启会路由回 manager 收件箱。这通常是想要的；但如果停机时间长，
   manager 一上来会扎堆收到一堆 worker 回卡

## 不在范围

- 残留的 lark-cli `+subscribe` 子进程清理：看 [orphan_subscribe_reap.md](orphan_subscribe_reap.md)
- watchdog 自动 respawn router 的逻辑：看 [team_lifecycle.md](team_lifecycle.md)

## 证据（跑的时候填）

```
- 第一次 router 启动 T_first: …
- 期间漏掉条数 N: …
- 重启 T_restart: …
- catchup 日志行: …
- 各收件箱是否拿到漏掉的消息: pass / fail
- 末态 router.cursor: …
- 备注: …
```
