# 技术短板诊断 + ACP 迁移方案

> 最近更新：2026-07-04
> 结论：ClaudeTeam 所有通信类技术债的共同根因是「拿 TUI 当 API」——用 tmux send-keys 写、抓屏 diff 读。ACP（Agent Client Protocol，JSON-RPC over stdio）是这个根因的结构性解法，且 2026 年生态已成熟（claude/codex/gemini/openclaw 都有 ACP 通路）。建议以 Runner 抽象做渐进迁移，tmux 路线保留为 ACP 不支持 CLI 的兜底。

## 一、技术短板清单（全部指向同一根因）

现有架构把每个 agent CLI 当成「屏幕上的 TUI」操作，于是每一层都在为「没有协议」付补偿成本：

| # | 短板 | 代码证据 |
|---|------|---------|
| 1 | **写入无协议**：注入靠 tmux send-keys，每个 CLI 要试一串提交键（M-Enter/Enter/C-m/C-j），还有 re-nudge hack（kimi 会把重发的提交键当 interrupt） | `agents/base.py` `submit_keys()` / `MULTILINE_SUBMIT_KEYS` / `resubmit_on_idle()` |
| 2 | **投递无 ACK**：`tmux_inject` 返回的 bool 只说明 send-keys 执行了，agent 是否真收到只能靠「注入后屏幕动没动」猜 | `feishu/deliver.py` `_inject_to_pane`、`runtime/wake.py` `inject_and_confirm` + `pane_probe.changed_since` |
| 3 | **忙闲靠抓屏 diff**：0.4s 双抓屏比较判断 busy/idle；spinner、时钟都算「忙」，卡死的 LLM 调用只要 UI 还有动画就永远 BUSY，watchdog 无法识别 | `runtime/pane_probe.py` `changed()` / `_classify()` |
| 4 | **回复通道靠 agent 自觉**：每条注入消息前面挂 prompt hint 教 agent「用 `claudeteam say` 回群、用 `claudeteam read` 销 inbox」——LLM 忘了 hint，回复就消失在 pane 里没人看见；「要不要抄送 manager」靠关键词猜（`_SUMMARY_CUE_TOKENS`） | `feishu/deliver.py` `_compose_inject_text` / `_wants_manager_summary` |
| 5 | **spawn 复杂度爆炸**：lifecycle.py 535 行，大半在对付 TUI 首启对话框（theme picker / trust dialog / auth picker，60s ready timeout + 1Hz auto-Enter）和 per-CLI 凭据播种（keychain 提取、symlink vs copy、oauthAccount 种子文件） | `runtime/lifecycle.py` `provision_pane` / `_ensure_claude_agent_home`、`agents/base.py` `ready_markers()` |
| 6 | **用量/上下文不可观测**：无实时 token 用量、无 context 余量信号；`usage` 命令靠 ccusage 离线扫 session logs | `commands/usage.py` |
| 7 | **权限审批不可达**：agent 全部预授权（settings.json 种子 allow Bash/Edit/Read/Write + skipDangerousModePermissionPrompt），危险操作没有人工审批通道 | `runtime/lifecycle.py` settings.json 种子 |
| 8 | **中断非确定性**：`/stop` 发 Escape 键，依赖各 TUI 恰好把 Esc 实现为 interrupt | `agents/base.py` `interrupt_keys()` 的长注释本身就是证据 |

defects-and-fix-plan.md 里的 T1（投递状态机）和 T2（心跳）是对 #2/#3 的**补丁式**修法；ACP 是**结构性**修法。

## 二、ACP 是什么，为什么现在能用

[ACP](https://agentclientprotocol.com/)（Agent Client Protocol）：Zed 2025-08 发起、JetBrains 2026-02 共同维护的开放协议，JSON-RPC over stdio，定位是「agent 界的 LSP」。核心方法：

- `initialize` / `session/new` / `session/load`（会话生命周期）
- `session/prompt` → 流式 `session/update` 通知（`agent_message_chunk`、`tool_call`、`tool_call_update`、`plan`）→ 带 `stopReason` 的响应（**turn 生命周期 = 忙闲状态白送**）
- `session/cancel`（确定性中断）
- `session/request_permission`（权限审批回传给客户端）
- usage/session_info 更新通知（用量可观测）

2026-07 支持现状：Gemini CLI 原生 `--acp`；Claude Code 经官方 `claude-agent-acp` 适配器；Codex 经 `codex-acp`；[OpenClaw `openclaw acp`](https://docs.openclaw.ai/cli/acp)；Cursor、Copilot（preview）、Cline 等也在列。**我们 12 个 adapter 里最重要的 4 个（claude-code、codex、gemini、openclaw）都有 ACP 通路**；kimi/qwen/minimax/trae 等暂无 → 需要混合架构。

### ACP 逐项对应我们的短板

| 短板 | ACP 解法 |
|------|---------|
| #1 写入无协议 | `session/prompt` 是 JSON-RPC 请求，没有提交键、转义、时序问题 |
| #2 投递无 ACK | prompt 有响应；投递状态机（T1）的 ACK 端白送 |
| #3 忙闲抓屏 | turn 生命周期 + `stopReason` = 精确的 busy/idle/error；T2 心跳白送 |
| #4 回复靠自觉 | `agent_message_chunk` 流由 harness 直接收，转发飞书不依赖 agent 记得跑 `say` |
| #5 spawn 对话框地狱 | headless 无 TUI：theme/trust/auth 对话框、ready_markers、auto-Enter 全部消失 |
| #6 用量不可观测 | usage 更新通知 → `/usage` 卡片实时化 |
| #7 权限不可达 | `session/request_permission` → **飞书审批卡片：手机上批准 agent 危险操作 = 竞品没有的杀手级 feature** |
| #8 中断非确定 | `session/cancel` |

## 三、迁移方案（渐进，不推倒重来）

原则：不动 router 的纯决策层（`classify_event` 不变），只替换 deliver 的「执行侧」。引入 **Runner 抽象**，tmux 路线降级为兜底而非删除。

> ✅ 2026-07-05（二轮）高强度 code-review + 修复：8 视角并行审查全分支 diff，28 候选去重后确认 15 项并全部修复（最重：/send 对 ACP agent 静默丢消息、AcpHost 启动时冻结花名册导致运行中 hire 的 agent 无消费者、/shutdown 关不掉住在 router 里的 ACP agent、worker/control 线程竞态、identity 失败的 session 被永久复用）。另做效率收敛（轮询 stat 快路径、standup 单次 inbox 解析、probe 单读）。1191 测试 0 失败。剩余记录：F-9 kimi 掉字待办、`recycle/cancel 回执无法感知消费者存在`为已知限制（由花名册热同步大幅缓解）。
>
> ✅ 2026-07-05 验收通过：零背景用户模拟验收（离线飞书 harness + 真实 claude-sonnet/codex/kimi 混编）10 项清单 9 PASS，唯一 FAIL（kimi 提交键 F-1）及 F-2~F-8 已修复复验，全量 1183 测试 0 失败。核心保证实测兑现：router kill -9 后 ~26s 自愈且消息恰好一次、/stop 确定性 cancelled、standup 2 分钟节拍精准。待办：F-9 kimi pane 长文本疑似掉字（中置信度，建议改 paste-buffer 注入）；真飞书链路复验需要老板扫一次码。
>
> 🟨 2026-07-04 实施中（分支 feat/acp-runner）：阶段 0-2 已落地——runtime/acp.py（JSON-RPC 客户端）、store/acp_queue（磁盘投递队列 = 跨进程 + 崩溃安全，代替原方案的进程内 Runner 对象）、runtime/acp_host（router 内 per-agent worker）、deliver/send//stop//team/peek/restart/health 全部接线，viewer pane 保留。真实 claude-code-acp 0.16.1 + codex-acp 冒烟通过（loadSession 可用）。与原方案的偏差：没有建正式的 Runner 抽象类（two-use rule——deliver 里一个 if 分支足够），"spawn 队列宿主"从设想的独立 daemon 简化为 router 内线程。

### 阶段 0 — Spike（小）✅（并入实施）

- 新建 `runtime/acp.py`：stdlib-only 的 JSON-RPC stdio 客户端（≈200 LOC：Popen + 逐行 read/write + request-id 配对 + 通知回调）。符合仓库「无依赖」原则，不引第三方包。
- 跑通一条链：spawn `gemini --acp`（或 `claude-agent-acp`）→ `initialize` → `session/new` → `session/prompt("你好")` → 收 update 流 → 拿到 stopReason。
- 产出 go/no-go 判断：延迟、稳定性、claude-agent-acp 的权限模型是否够用。

### 阶段 1 — Runner 抽象 ⬜

- 新建 `runtime/runner.py` 接口，方法对齐 deliver/watchdog 现有需求：
  `spawn(agent)` / `deliver(agent, text) -> Ack` / `state(agent) -> busy|idle|dead` / `interrupt(agent)` / `stop(agent)`
- `TmuxPaneRunner`：封装现有 `tmux.inject` + `wake` + `pane_probe`（纯搬运，行为不变）。
- `AcpRunner`：每 agent 一个常驻 ACP 子进程 + session；`deliver` = `session/prompt`（排队：turn 进行中时新消息入队，turn 结束后追加发送——语义和现在「注入到 pane」一致但有序有 ACK）。
- `claudeteam.toml` per-agent 加 `runner = "acp" | "tmux"`（默认 tmux，行为零变化）。
- `feishu/deliver.py` 的 `_inject_to_pane` 改为 `runner.deliver(...)`；watchdog 按 runner 分流（ACP agent 的保活 = 子进程存活 + `session/load` 恢复）。

### 阶段 2 — 状态与可观测性接线 ⬜

- `session/update` → `status.json`（T2 心跳完成）+ `logs.jsonl` 事件流（T4 日志聚合的数据源）。
- prompt 响应/错误 → inbox 行的 ack 状态（T1 投递状态机完成）。
- usage 通知 → `/usage` 飞书卡片实时化。

### 阶段 3 — 可见性与权限（差异化收割）⬜

- **保住「看得见的办公室」**：tmux pane 保留为只读 viewer，tail 渲染该 agent 的 ACP 输出流。操作者仍能 attach 围观每个员工干活——这是产品性格，不能丢。
- `session/request_permission` → 飞书审批卡片（批准/拒绝按钮）。移动端审批 agent 的危险操作，Multica/Claude Squad 都没有。

### 阶段 4 — 收尾 ⬜

- claude-code / codex / gemini / openclaw 默认 `runner = "acp"`；kimi/qwen/minimax/trae 留 tmux。
- ACP agents 不再需要 `submit_keys` / `resubmit_on_idle` / `ready_markers` / 抓屏 probe；`base.py` 相应瘦身（保留给 tmux 兜底 CLI）。

### 风险与对策

| 风险 | 对策 |
|------|------|
| `claude-agent-acp` / `codex-acp` 是外部包，版本跟进成本 | adapter 里 pin 版本；spike 阶段评估其成熟度 |
| ACP session 挂在 router 进程树上，router 重启丢会话 | watchdog 转型为 ACP 子进程 supervisor；`session/load` 恢复（各 agent 支持度在 spike 中验证，OpenClaw 的 `loadSession` 只对 bridge 建的会话完整回放） |
| 失去「人直接在 pane 里打字干预」 | 混合模式保留；viewer pane 只读，干预走飞书或 `claudeteam send` |
| 每 agent 常驻一个 node 适配进程，内存开销 | 与现状每 pane 一个 CLI 进程相当，预计中性 |

## 四、与既有施工序列（defects-and-fix-plan.md）的关系

- **T0（mock E2E 框架）不变，仍然第一**——而且 mock 一个 ACP server（假 JSON-RPC stdio 进程）比 mock tmux 更容易，测试价值更高。
- T0 之后插入 **ACP spike（阶段 0）**，用结果决策：spike 顺利 → T1/T2 直接在 AcpRunner 上实现（结构性解法），tmux 路线只做最小兜底修补；spike 不顺 → 回退到原 T1/T2 补丁路线，方案不作废。
- T3（manager 降级）、T4（日志聚合）在两条路线下都成立，ACP 路线下数据源更干净。
