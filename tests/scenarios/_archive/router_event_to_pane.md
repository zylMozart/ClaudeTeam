# 路由器：飞书消息 → 本地收件箱 + manager pane

## 场景

最关键的端到端路径：老板（或任意用户）在飞书群里发一条消息 → router 守护进程
经 lark-cli 事件订阅接收 → 写入 manager 的本地收件箱 + 注入到 manager tmux pane。
这是外部消息进入系统的唯一通路。

R174（提交 `9e43309`）之后，**所有人话不论加不加 @ 都只路由到 manager**；
worker 自己发的卡片是唯一的例外，会被路由回 manager 的收件箱。

## 范围

- 类型：本机端到端（飞书 + tmux + 真 CLI）
- 凭证：lark-cli profile 已授权，chat_id 已配，机器人有 im:message scope
- 操作员：老板（或用 `--as user` 模拟，见 [host_smoke.md](host_smoke.md) 第 2 节）

## 前置条件

- `runtime_config.json` 里写了 `chat_id`（`oc_xxx`）与 `lark_profile`
- `team.json` 里至少有 `manager`
- `claudeteam start` 已经把 tmux 会话和 manager pane 起好，CLI 横幅已出
- 飞书群里没有未读消息
- （可选）已经按 [host_smoke.md](host_smoke.md) 第 2 节做完用户 OAuth，可以 `--as user` 模拟用户发消息

## 操作

```bash
# 终端 A：起 router 守护进程
claudeteam up                 # 顺带把 watchdog 一起拉起
# 或单独：claudeteam router

# 终端 B（或老板直接在飞书 App 里手动）：发各种类型的消息
CHAT="$(python3 -c 'import json; print(json.load(open("runtime_config.json"))["chat_id"])')"
SEND() { LARK_CLI_NO_PROXY=1 lark-cli im +messages-send --chat-id "$CHAT" --text "$1" --as user --format json | python3 -c 'import json,sys;print(json.load(sys.stdin).get("data",{}).get("message_id",""))'; }
```

## 期望

### 用例 A：默认路由（普通文本）

发：`SEND "smoke ping"`

1. 提交 `c0996a5` 之后，`state/router.log` 多出 `[event] action=route msg=om_xxx text='smoke ping' sender=?` 一行
2. 5 秒内（lark-cli 推送典型延迟），`claudeteam inbox manager` 多出一行新未读：from=user，content=`smoke ping`
3. `tmux capture-pane -t <会话>:manager -p | tail -10` 看到 `smoke ping` 已被注入并提交，CLI 开始处理
4. `state/router.cursor` 推进到这条消息的 `(message_id, create_time)`

### 用例 B：R174 契约——@ 也只到 manager

下面四条全都期望 manager 收件箱拿到，**worker 收件箱不动**。这是 R174 的核心契约。

```bash
SEND "@worker_cc 你看下 README"
SEND "@worker_cc @worker_codex 都过来"
SEND "@team 全员同步进度"
SEND "全体注意，今晚 18:00 review"
```

验证：

```bash
claudeteam inbox manager       # 应有 4 条新未读，正文里包含原始 @
claudeteam inbox worker_cc     # 应有 0 条新
claudeteam inbox worker_codex  # 应有 0 条新
```

`state/router.log` 里这 4 条都应是 `action=route`，目标永远是 manager。
**不应**出现 `action=broadcast`——R174 之后路由器不再产生 BROADCAST。

### 用例 C：R174 例外——worker 卡片回路 manager

```bash
# 让 worker_cc 自己往群里发卡：
claudeteam say worker_cc "auth 模块完成" --card
```

1. 群里出现 worker_cc 配色（绿色）的卡，message_id 是 `om_xxx`
2. router 把这条机器人发的卡路由到 manager 收件箱：sender=worker_cc，content=卡片内容
3. `state/router.log` 显示 `action=route targets=[manager] sender=worker_cc`
4. manager pane 处理这条新收件箱

对照实验：

```bash
claudeteam say manager "我同步一下进展" --card
```

应当 DROP，`state/router.log` 里出现 `action=drop reason=bot_self`，
manager 自己**不会**收到自己发的卡（避免回声循环）。

### 用例 D：去重与跨群

- 同一条 message_id 重复推送 → 只处理一次，`state/router.log` 里第二次显示 `action=drop reason=dedup`
- 别的群的消息（`chat_id` 不匹配） → `action=drop reason=cross_team`
- 空文本（msg_type=image / sticker 等被处理成空字符串） → `action=drop reason=empty`

### 用例 E：守护进程退出

`Ctrl-C` 终端 A 的 router 后：

- router 退出码 0
- `state/router.pid` 被清理
- 子进程 `lark-cli +subscribe` 整组被一起回收（提交 `9e43309` 之前会留孤儿，[orphan_subscribe_reap.md](orphan_subscribe_reap.md) 是兜底机制）

## 已知风险

1. **`state/router.log` 是新加的**（提交 `c0996a5`）。在它之前所有运行中失败都没有日志，要靠 `claudeteam down && claudeteam up` 重启清状态来恢复
2. **客户端 OAuth 只走过一次的 deploy**：`SEND` 函数依赖 `--as user`，没做过 [host_smoke.md](host_smoke.md) 第 2 节的设备授权流程时这条会失败
3. **lark-cli 长连接偶尔会静默断开**：watchdog 子线程每 20 秒探活，子进程 exit 后整 router daemon 会被 SIGTERM，watchdog 主进程随后 respawn。一次断开通常 30 秒内恢复

## 不在范围

- agent 之间互发的 peer 路径：看 [local_message_cycle.md](local_message_cycle.md)
- 路由器死掉重启不丢消息：看 [router_catchup.md](router_catchup.md)
- 残留 lark-cli 子进程的清理：看 [orphan_subscribe_reap.md](orphan_subscribe_reap.md)

## 证据（跑的时候填）

```
- chat_id: oc_xxx
- 用例 A 老板发出时间 T_send: …
- 用例 A router.log 出 [event] 时间 T_log: …
- 用例 A pane 注入时间 T_pane: …
- 总延迟 (T_pane − T_send): …s
- 用例 B 的 4 条: manager 收件箱新增数 = ___，worker 收件箱新增数 = ___
- 用例 C 的 worker 卡 message_id: om_xxx；manager 收件箱是否新增: yes/no
- 用例 C 的 manager 卡 message_id: om_xxx；router.log 应有 drop reason=bot_self
- 结果: pass / fail
- 备注: …
```
