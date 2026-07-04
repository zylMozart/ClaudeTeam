# 竞品格局与差异化定位

> 最近更新：2026-07-04
> 结论：赛道极度拥挤（awesome 列表 100+ 项目），但 ClaudeTeam 的「飞书原生 + 常驻团队 + 轻部署」组合定位成立；真正的短板是可靠性工程，不是定位。

## 赛道分类

多 CLI agent 编排赛道大致分四类，ClaudeTeam 属于第四类：

### 1. 控制平面/管理平台型（最直接的竞品）

**[Multica](https://github.com/multica-ai/multica)** — 头部玩家，39k stars / 4.9k forks（2026-07 调研）

- 架构：Next.js 16 前端 + Go 后端（Chi/sqlc/websocket）+ PostgreSQL 17 (pgvector)
- 本地 daemon 自动发现 PATH 上已装的 CLI，支持 13 种（Claude Code、Codex、Copilot CLI、OpenClaw、OpenCode、Hermes、Gemini、Pi、Cursor Agent、Kimi、Kiro、Qoder、Trae）
- 任务模型：issue 派给单 agent 或 Squad（leader 内部再分派）；WebSocket 实时进度流
- Skills 系统：把可复用流程（部署、迁移、code review）沉淀为团队技能
- Autopilots：cron / webhook / 手动触发的定时任务
- 部署：SaaS（Multica Cloud）或自托管（Docker Compose / K8s Helm）
- 定位：「Jira/Linear 与 coding agent runtime 之间的协作控制平面」
- 弱点：自托管重（Docker + PG + Go 后端）；任务派单模型而非常驻团队

**[Centaur](https://github.com/paradigmxyz/centaur)** — Paradigm 出品，MIT

- 与我们理念最接近的项目：**Slack 原生，一个 Slack thread = 一个隔离 agent 会话**
- 每个会话跑在独立 K8s 沙箱容器（shell/git/Python/Node）
- 支持 Amp、Claude Code、Codex 等 CLI
- 相当于「Slack 版的 ClaudeTeam」，验证了 IM-as-console 模式成立
- 弱点：需要 K8s，运维门槛高；无飞书/中国生态

### 2. 并行会话管理器型

- **Claude Squad**（~5.8k stars，开源社区最大）：Go TUI + tmux + git worktree，每任务隔离工作区
- **Conductor**：Mac 桌面 app，worktree + diff viewer + PR 流
- **Crystal → Nimbalyst**：Crystal 2026-02 弃更，导流到闭源收费的 Nimbalyst（有 iPhone 伴侣 app）
- **NTM / dmux**：tmux 系命令中心，broadcast prompt + 冲突检测

### 3. 看板/任务队列型

- **Vibe Kanban**：背后公司 Bloop 2026-04 倒闭，转社区维护 —— 赛道惨烈程度的注脚
- **agent-kanban / openkanban / dorothy** 等大量同质化项目

### 4. 常驻团队/Swarm 型（我们所在类别，尚无绝对赢家）

- **claude-flow**：多 agent swarm 部署
- **gastown**：持久工作追踪
- **shire**：带 mailbox 的持久 agent 工作区（和我们的 inbox 机制思路相同）
- **multi-agent-shogun**：tmux 层级编排（武士等级制），东亚背景

### 平台级威胁：Claude Code 官方 Agent Teams

[官方文档](https://code.claude.com/docs/en/agent-teams)，2026-02 上线，实验性（需 `CLAUDE_CODE_EXPERIMENTAL_AGENT_TEAMS=1`）。

原生支持：lead + teammates、共享任务列表（文件锁防抢）、mailbox 通信、tmux/iTerm2 分屏、hooks 质量门禁。

当前限制（我们的护城河窗口期）：
- 只支持 Claude Code 自家，无多 CLI 混编
- 一个 session 一个团队，不能跨 session 存续；`/resume` 后 in-process teammates 丢失
- 无远程/IM 控制，必须守在终端前
- lead 固定不可转移，teammate 不能再 spawn 团队

⚠️ 风险判断：官方补齐基础能力有节奏，纯「多 pane 编排」的价值会被逐步吃掉。我们的防御必须建立在官方不会做的事上：飞书生态、多 CLI 混编、跨 session 常驻。

## 我们的差异化（四点）

1. **飞书原生 + 移动端遥控**。西方竞品全是 Slack/Web UI/桌面 app，没有一个做飞书。手机在飞书群里指挥 agent 团队是 Multica、Claude Squad 都给不了的体验。Centaur 证明了 IM-as-console 成立，我们是这个模式在飞书生态的唯一实现。
2. **常驻团队而非一次性任务**。Multica/Vibe Kanban 是「派单-执行-完事」；我们是 agent 持续在线、watchdog 保活、记忆跨重启存续的「长期雇员」模型。`/hire` `/fire` 中途改编制是独有交互。
3. **多 CLI 混编 + 零基础设施**。12 种 CLI adapter 混编一个团队（manager 用 Claude、worker 用便宜的 DeepSeek）；纯文件状态 + tmux，无 PG、无 Web 服务，8GB 内存单机可跑。对比 Multica 自托管需要 Docker + PG 17 + Go 后端。
4. **非编程团队模板**。content-ops / marketing-growth / automated-research 模板瞄准非 coding 场景，而几乎所有竞品死磕 coding。这个方向竞争最空旷。

**一句话定位：「飞书里的常驻 AI 员工团队」—— 对标 Multica 的重平台路线，我们走轻部署 + IM 原生 + 中国生态。**

## 参考来源（调研日期 2026-07-04）

- [Multica GitHub](https://github.com/multica-ai/multica) / [multica.ai](https://multica.ai/)
- [awesome-agent-orchestrators](https://github.com/andyrewlee/awesome-agent-orchestrators)（赛道全景列表）
- [Centaur GitHub](https://github.com/paradigmxyz/centaur) / [Paradigm 发布文](https://www.paradigm.xyz/2026/05/open-sourcing-centaur-multiplayer-self-hosted-secure-agents)
- [Claude Code Agent Teams 文档](https://code.claude.com/docs/en/agent-teams)
- [Nimbalyst：2026 多 agent 工具对比](https://nimbalyst.com/blog/best-multi-agent-coding-tools-2026/)
- [Augment：9 个开源编排器盘点](https://www.augmentcode.com/tools/open-source-agent-orchestrators)
