# feishu/ — 飞书事件入站 → 路由 → 投递（消息主链路）

改这个目录 = 改产品的心脏。三段式结构是刻意的，别合并：

```
subscribe.py   解析 sidecar 的 NDJSON 行（I/O 边界）
router.py      classify_event() 纯函数：DROP / ROUTE / BROADCAST / SLASH
deliver.py     apply() 唯一的副作用层：写 inbox + 按 runner 投递
```

## 不变量（每条都有真实事故背书）

- **router.py 保持纯函数**。不许读磁盘、不许发请求——决策可测试性
  全靠这个。新的路由规则 = 给 classify_event 加分支 + 单测，别处不动。
- **deliver 先写 inbox，再尝试投递**。inbox 行是 canonical record，
  投递（acp 队列 / tmux inject）失败时消息也不能丢。
- **投递按 runner 分岔**：`config.agent_runner(agent)` → "acp" 走
  `store/acp_queue.enqueue`（有 ACK，崩溃安全），"tmux" 走
  lazy-wake + `tmux.inject`（尽力而为）。改投递逻辑两条路都要想到。
- **retired（已停止）的 agent 只写 inbox，绝不投递/唤醒**。
- 去重靠 `msg_id` 的 seen 集合（router 进程内 + `state/router.seen`
  持久化）——catchup 重放全指望它，动它前先读 `commands/router.py`。

## slash.py（群聊 / 命令）

- 加命令的四步 checklist 在根 CLAUDE.md "Maintenance recipes"，
  照做。handler 返回 str（文本）或 dict（飞书 v2 卡片）。
- slash 在 router 层执行、零 LLM 参与，所以 handler 必须快（<2s），
  慢活儿丢给 agent（enqueue / inject）或 `ctx.background`。
- 涉及"戳 agent"的 handler 一律先分岔 runner（`_handle_stop` /
  `_handle_clear` 是范例）。

## sidecar

`scripts/feishu_channel/sidecar.js` 是独立 node 工程（官方
`@larksuite/channel` 的薄包装），不 import claudeteam。协议是
NDJSON（lark-cli --compact 平铺形状）——改输出形状要同步改
subscribe 的解析和 `tests/integration/test_*chain*.py` 的事件构造。
