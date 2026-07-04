# 缺陷诊断与修复优先级

> 最近更新：2026-07-04
> 结论：差异化定位不用大改，优先修可靠性。「常驻团队」是核心卖点，消息丢失和 manager 失联直接打脸这个卖点。施工顺序 ≠ 严重度顺序，见下方「施工序列」。
> 状态标记：⬜ 未开始 / 🟨 进行中 / ✅ 已完成 / ❌ 已否决

## 施工序列（按依赖关系排序，2026-07-04 定）

判断「早修」的标准：凡是打脸「常驻可靠团队」核心卖点的都早修（T1/T2/T3），T0 是它们的前置投资。

| 序 | 任务 | 原编号 | 成本 | 排序理由 |
|---|------|-------|------|---------|
| T0 🟨 | Mock sidecar E2E 测试框架 | P1-2 | S | fake ACP agent（tests/fake_acp_agent.py）已落地并驱动 22 个新单测；mock sidecar 事件注入待补 |
| T1 🟨 | 消息投递状态机 + 重试 | P0-1 | M | **经 ACP 路线落地**（feat/acp-runner 分支）：store/acp_queue = pending→prompting→done/failed 状态机，崩溃后 recover_stuck 重臂，ACK 写 logs.jsonl |
| T2 🟨 | Agent 心跳上报 | P0-2 | M | **经 ACP 路线落地**：turn 生命周期 + 流式 update 驱动 heartbeat/status，acp_host.probe 替代抓屏（ACP agents）；tmux 兜底 agents 仍抓屏 |
| T3 ⬜ | Manager 单点降级 | P0-3 | S-M | 依赖 T2 心跳信号，有信号后本体不难 |
| T4 ⬜ | 日志聚合 | P1-1 | S | 复用 T1 的持久化 DeliveryReport 和 T2 的 status 数据，顺手收割 |
| T5 ⬜ | 小债打包（msg_id 过期 / @解析 / quoting 审计） | P1-3 | S | 攒一个 PR 一起清 |
| — | 战略项（chat 层抽象、Windows） | P2 | L | 不动手，等用户来源数据再决策 |

里程碑：T0+T1 完成 = 「消息不可靠」问题闭环；T2+T3 完成 = 「团队失联」问题闭环。

> ⚠️ 2026-07-04 更新：T1/T2 存在结构性替代方案——ACP 迁移（见 [acp-migration.md](acp-migration.md)）。建议 T0 之后先做 ACP spike，用结果决定 T1/T2 走补丁路线还是 AcpRunner 路线。

## P0 — 可靠性硬伤（用户流失的直接原因）

### ⬜ P0-1 消息投递无状态追踪

- **现状**：router 崩溃时消息「已处理/未处理」状态不落盘，可能重复注入或丢失；tmux send-keys 注入失败只记 `failed_inject` 不重试，依赖 watchdog 下次 respawn 兜底（`src/claudeteam/feishu/deliver.py`）
- **修法**：给 inbox 加投递状态机（pending → injected → acked）+ 失败重试队列；DeliveryReport 持久化到 logs.jsonl
- **验收**：kill -9 router 进程后重启，消息不丢不重

### ⬜ P0-2 Agent 忙闲判断靠 pane 抓屏猜

- **现状**：无 heartbeat；watchdog 只知道进程死活，不知道 agent 是否卡死在 LLM 调用上；`peek` 和 `/team` 卡片靠 tmux capture-pane 的屏幕内容推断状态（`src/claudeteam/runtime/pane_probe.py`、`watchdog.py`）
- **修法**：Claude Code 用 hooks（Stop / PostToolUse）主动上报心跳到 status.json；其他 CLI 退化为抓屏推断，status 中标注置信度
- **验收**：agent 卡死 5 分钟内 watchdog 能识别并处置（而不是等进程死亡）

### ⬜ P0-3 Manager 单点故障

- **现状**：所有群消息只进 manager（default_target 路由），manager 挂了整个团队失聪
- **修法**：router 层加 manager 健康检查；宕机时降级策略——直接广播给 workers 或在飞书群发报警卡片
- **验收**：fire manager 后群里发消息，团队仍有响应（报警或降级路由）

## P1 — 竞争力短板

### ⬜ P1-1 日志碎片化、不可调试

- **现状**：pane 历史（tmux）、watchdog log、logs.jsonl 三处分散，无统一视图；`CLAUDETEAM_DEBUG=1` 只输出 Python traceback。Multica 的核心卖点之一恰是 operational visibility
- **修法**（先做便宜的）：`claudeteam logs --follow` 聚合命令 + 飞书 `/debug` 卡片显示最近 router 决策和投递结果

### ⬜ P1-2 无 E2E 自动化测试

- **现状**：72 个测试文件全是进程内模拟；`tests/scenarios/*.md` 是人工回归剧本
- **修法**：mock sidecar（伪造 NDJSON 事件流）驱动的自动化 E2E，覆盖 router→deliver→inbox 全链路；接入 CI

### ⬜ P1-3 小型技术债

- msg_id 去重 seen 集合无过期，理论上无限增长（`feishu/subscribe.py`）
- CLI adapter 新增需改 `agents/` 代码，无插件/注册机制
- `@target` 解析用正则推断 token 边界，奇异字符名/emoji 可能混淆（`feishu/router.py` `_tokenize`）
- 消息注入 tmux send-keys 缺乏系统性 quoting 审计（潜在注入面）

## P2 — 战略选择（先论证再动手）

### ⬜ P2-1 Chat 层抽象化 vs 深耕国内 IM

- 飞书既是差异化也是天花板。两条路二选一：
  - **出海**：把 `src/claudeteam/feishu/` 抽成 channel 接口，加 Slack/Telegram adapter（对标 Centaur）
  - **深耕国内**：优先钉钉/企业微信 adapter
- 决策依据：先看现有用户/star 来源分布再定

### ⬜ P2-2 Windows 支持

- tmux 硬依赖导致 Windows 不可用（WSL2 变通）；国内用户 Windows 占比高，伤害真实存在，但修复成本高（需抽象 pane 运行时）
- 暂缓，除非国内深耕路线确定且用户反馈集中

## 修复顺序建议

P0-1 → P0-2 → P0-3 → P1-1 → P1-2，每项落地后更新本文件状态标记。
