# Router: Feishu chat → local inbox + manager pane

## 场景
最关键的端到端：老板（或任意用户）在飞书群里发一条消息 → router daemon 经 lark-cli event 接收 → 写入 manager 的本地 inbox + 注入到 manager tmux pane。这是消息从外部进入系统的唯一通路。

## 范围
- 类型：host-live (Feishu + tmux + 一个真 CLI)
- 凭证：lark-cli profile 已 auth + chat_id 已 setup + bot 有 im:message scope
- 操作员：boss

## Given
- runtime_config.json 含 `chat_id` (oc_xxx) 和 `lark_profile`
- team.json 含至少 `manager`
- `claudeteam start` 已起来 → tmux session 中 manager pane 进入 CLI banner
- 飞书群里没有未读消息

## When

```bash
# 终端 A：启 router daemon
claudeteam router

# 终端 B（或老板从飞书 app）：发一条群消息
# 简单消息（默认路由到 manager）
[boss types in chat]: smoke ping
```

## Then
1. **router** stdout 立即出现 `🚀 router subscribing on chat oc_xxx`
2. 5 秒内（典型 lark-cli 推送延迟） router stdout 出现该消息的处理痕迹（`handled=N` 增长）
3. `claudeteam inbox manager` 列出新消息：from=user，content=smoke ping
4. `tmux capture-pane -t <session>:manager -p | tail -10` 看到 `smoke ping` 文本被注入并提交（CLI 已开始处理）
5. **Ctrl-C** router 退出 0；`state/router.pid` 被清理

## 反例情况

- chat_id 为空 → router 退出 1
- lark-cli 不在 PATH → router 退出 1
- 同一条 message_id 重复推送 → 只处理一次（dedup 命中，`drops_by_reason.dedup` 增加）
- 别的群（chat_id 不匹配）的消息 → 丢弃，`drops_by_reason.cross_team` 增加

## 证据（执行时填）

```
- chat_id: oc_xxx
- T_send: …
- T_router_handled: …
- T_pane_inject: …
- 总延迟 (T_pane - T_send): …s
- 结果: pass | fail
- 后续: …
```
