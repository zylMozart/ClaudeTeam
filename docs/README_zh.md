<h1 align="center">ClaudeTeam</h1>

<p align="center"><b>把一句话，变成一支动态 AI 团队。</b></p>

<p align="center">
  <img src="media/team-templates.png" alt="ClaudeTeam 五套现成领域团队——软件开发、自动化科研、营销增长、数据分析、内容运营——每套都是主管 + 员工协作" width="900" />
</p>

<p align="center">
  <a href="../README.md">English</a> · <b>简体中文</b>
</p>

<p align="center"><sub><a href="../templates/"><code>templates/</code></a> 里有五套现成领域团队——软件开发 · 自动化科研 · 营销增长 · 数据分析 · 内容运营。拷一套改改,或让主管现搭一支。</sub></p>

<p align="center">
  <a href="../LICENSE"><img src="https://img.shields.io/badge/license-MIT-blue.svg" alt="MIT License" /></a>
  <img src="https://img.shields.io/badge/python-3.9%2B-blue.svg" alt="Python 3.9+" />
  <a href="https://github.com/zylMozart/ClaudeTeam/actions/workflows/ci.yml"><img src="https://github.com/zylMozart/ClaudeTeam/actions/workflows/ci.yml/badge.svg" alt="tests" /></a>
  <a href="DEPLOYMENT_zh.md"><img src="https://img.shields.io/badge/docs-deployment-success.svg" alt="部署文档" /></a>
  <img src="https://img.shields.io/badge/chat-Feishu-1a73e8.svg" alt="飞书" />
</p>

<p align="center">
  主管 agent 按需招工、裁员，把员工编排成循环或工作流，跨任务记忆，会反思、会进化 ——
  而你，在手机上的一个飞书群里遥控整支班子。
</p>

> **一键部署 —— 把下面这段话粘给你的 coding agent（Claude Code / Codex / Kimi / Gemini / Qwen 都行）：**
>
> ```
> 克隆 https://github.com/zylMozart/ClaudeTeam.git，读 docs/DEPLOYMENT_zh.md，按里面
> 「让 coding agent 替你部署」那套协议走：先问我入场问题（我装了并登录了哪些 CLI、
> 飞书用 quick 还是免 @），起团队，再逐个 agent 验证过了，才告诉我搞定。
> ```

---

## ✨ 为什么用 ClaudeTeam

- **🔁 Agent Loop —— 常驻在线。** Agent 跑在 tmux 里**持续运行**，不是一次性。主管循环盯着群 + 收件箱，看门狗保活每个 pane（带冷却的自动重启）—— 团队是**你随时能对话的常驻班子**，不是跑一次就完的脚本。
- **👥 动态招工、裁员与工作流。** `/hire` 起一个新 agent、`/fire` 退一个 —— 团队在**任务中途**自我重组。主管按这条 prompt 的需要选拓扑：并行铺开、跑序列、或循环到达成目标。
- **🧠 持久记忆。** 每个 agent 的记忆扛得住 `/clear` 和 pane 重启，wake 时自动注入。团队还共享一份**经验池** + 一座可复用的**技能库**。
- **🌱 反思与进化。** Agent 复盘结果、把学到的东西沉淀回共享记忆 + 技能 —— 团队对重复性工作**越做越好**，而不是每次从零开始。
- **📱 飞书遥控。** 在手机群里指挥整支班子：用大白话跟主管说话（不用 `@`），看员工以卡片回报，用斜杠命令做运维。

> 跑在**你自己的** coding CLI 上 —— 自由混编（见下）。所有状态可在磁盘审计，不依赖任何远程数据库。

---

## 支持你的 CLI

下面这些都能作为 agent 跑在同一支团队里（见[适配表](#多-cli-适配)）：

**Claude Code · Codex · Gemini · Qwen · Kimi · MiniMax · opencode · CodeWhale · OpenClaw · Trae · Hermes · Pi**

默认团队全是 Claude Code，所以装个 `claude` 就能跑；其余都是可选的 —— 你有哪个就加哪个。

---

<p align="center">
  <img src="media/architecture.png" alt="ClaudeTeam —— 把一句话变成一支动态 AI 团队：主管 agent 选能力、按需招工裁员、用循环或工作流编排，带持久记忆和自我反思，全程在飞书里遥控" width="900" />
</p>

## 快速开始

前置依赖、安装、完整分步（host & Docker）都在 **[docs/DEPLOYMENT_zh.md](DEPLOYMENT_zh.md)**
—— 5 步、从上到下。装好之后的核心流程：

```bash
claudeteam init --no-connect       # 写 claudeteam.toml（默认：manager + 1 个 claude worker）
claudeteam feishu connect --quick  # 扫一次码、哪台机器都能跑——建好机器人 + 你的团队群（群里要 @bot）
claudeteam install-hooks
claudeteam up                      # 起团队；主管在群里发起全员点名
```

之后在群里直接发 `你好`（私聊不用 `@`；**`--quick` 模式下群里要 @bot**）。Agent **复用你本地已有
的 CLI 登录**；`health` 绿只代表基础设施起了，真正的判据是主管那轮**全员点名**。

> 想让机器人群里**不 @** 也能回？去掉 `--quick`：`claudeteam feishu connect` 会用浏览器建一个带
> `im:message.group_msg` 的自建应用。它要有桌面浏览器 —— 无头服务器上就在桌面机器上跑、再把凭据
> 拷过去 —— 见[飞书机器人配置](#飞书机器人配置)。

---

## 多 CLI 适配

同一个团队里员工可以用不同 CLI。各自的安装命令见
[DEPLOYMENT_zh.md 里的安装表](DEPLOYMENT_zh.md#agent-clis)，ClaudeTeam 只要它在 PATH 上即可。

| 适配器 | `cli` 标识 |
| --- | --- |
| Claude Code | `claude-code`（默认） |
| Codex CLI | `codex-cli` |
| Kimi Code | `kimi-code` |
| Gemini CLI | `gemini-cli` |
| Qwen Code | `qwen-code` |
| MiniMax Mini-Agent | `minimax` |
| opencode | `opencode` |
| CodeWhale | `codewhale` |
| OpenClaw | `openclaw` |
| Trae | `trae` |
| Hermes | `hermes` |
| Pi | `pi` |

后七个是 **OpenAI 兼容**（BYOK）：用 `OPENAI_BASE_URL` + `OPENAI_API_KEY` 指到任意端点。详见
[DEPLOYMENT_zh.md → *每个 agent 的模型后端*](DEPLOYMENT_zh.md)。

```toml
[team.agents.manager]
cli = "claude-code"
model = "opus"
role = "团队主管"

[team.agents.worker_codex]   # 装了 codex 再加
cli = "codex-cli"
role = "数据分析员工"
```

---

## 更多

- **ACP runner（claude-code / codex 默认）** —— CLI 以 headless 方式跑在 router 里，说 [Agent Client Protocol](https://agentclientprotocol.com/)：每条消息都进持久化队列、带投递 ACK（扛得住崩溃——把 router `kill -9` 也不丢一条、不重一条），忙/闲状态精确，`/stop` 是确定性的协议级中断。agent 的 tmux 窗口保留成一个实时的只读 transcript。不支持 ACP 的 CLI 照旧走经典 tmux pane runner。
- **Standup 定时汇报** —— 活儿在跑的时候，主管按定时器巡视每个 agent（默认 10 分钟，toml 里 `[standup]` 调），往群里发一条合并的进度汇报：谁在干什么、卡点、下一步。团队空闲 = 沉默；`/standup` 随时手动来一轮。
- **单接口路由** —— 群里任何消息都只进主管；员工不会直接收老板原话。主管是唯一调度入口。
- **单一配置文件** —— `claudeteam.toml`（Cargo 风格、可写注释）：chat_id / agents / 模型 / 卡片色 / publish 过滤都在一起。
- **团队模板** —— 从 [`templates/`](../templates/) 里现成的领域团队起步（软件开发 / 科研 / 营销 / 数据 / 内容）：一个 `claudeteam.toml` + 每个角色一份 **playbook**，会成为该 agent 的 `CLAUDE.md` / `AGENTS.md`。
- **`[chat.publish]` 过滤** —— 按 sender→receiver 维度控可见性，静默噪声但保审计。
- **每个 agent 自己的空间 + 共享大脑** —— 每个 agent 有自己的 `workspace/` 草稿区和隔离 CLI home；团队共享经验池（`remember --team`）和可复用 `skills/` 库，wake 时都注入。
- **群里斜杠命令** —— `/help /team /health /usage /tmux /send /compact /stop /clear /task /standup`，外加运维三条 `/restart /shutdown /login`。
- **几乎零依赖** —— 标准库 Python（仅在 Python < 3.11 上带一个 `tomli` 后备）；唯一外部 runtime 是 Node，跑内置飞书 sidecar 和 ACP 适配器（`lark-cli` 可选——只有 `--as user` 发消息需要它）。

---

## 飞书机器人配置

`claudeteam feishu connect` 有**三种模式** —— 都会建机器人、自动建好你的团队群、写好 `chat_id`：

- **`claudeteam feishu connect --quick`**（最省事的路）—— 一次扫码的**个人版应用**（device-flow
  二维码），**零控制台**，而且**哪台机器都能跑**（含无头服务器 / Docker 容器）。它建好机器人应用 +
  团队群（把你拉进群）+ 凭据 + `chat_id`。唯一的代价：个人版应用拿不到 `im:message.group_msg`，所以
  **群里要 @bot** 才会回 —— 私聊不受影响，先从这里起步完全可以。
- **`claudeteam feishu connect`**（不带 flag）—— 想让机器人群里**不 @** 也能回，就用它：浏览器自动化
  建**自建应用**（企业自建应用）。它开一个真实（有界面的）浏览器，驱动飞书开发者后台创建应用、导入
  权限 scope、订阅消息事件、并**发版**。成品带 `im:message.group_msg`，所以机器人**群里不 @ 也能回**。
  你只**扫一次**登录码，7 个控制台阶段自动跑；撞上后台改版会**自动回退到 `--manual`**。需要桌面浏览器。
- **`--manual`** —— 同样是自建应用，但在控制台里**一步步手动引导**（粘 App ID/Secret、点一键授权
  深链、发版）。无浏览器自动化；最稳的兜底。

> **无头环境？** `--quick` 在无头环境照样能跑。不带 flag 的浏览器自动化要桌面浏览器，所以无头服务器
> 上就在桌面机器跑 `connect`、再把存好的凭据（`state/feishu_app.json`）+ `chat_id` 拷过去。

→ 完整步骤（全部模式、权限、事件、发版）：**[docs/DEPLOYMENT_zh.md → 第 3 步](DEPLOYMENT_zh.md)**。

底层：`scripts/feishu_channel/` 下一个薄薄的 sidecar 包了官方
[`@larksuite/channel`](https://www.npmjs.com/package/@larksuite/channel) SDK，注册和 WebSocket
事件入站都走它。

---

## 文档

| 文档 | 内容 |
| --- | --- |
| [`docs/DEPLOYMENT_zh.md`](DEPLOYMENT_zh.md) | Host 部署（5 步）/ 配置 schema / 模型后端 / 多团队隔离 / 故障排查 |
| [`docs/DEPLOYMENT_docker_zh.md`](DEPLOYMENT_docker_zh.md) | Docker / 服务器部署 |
| [`CLAUDE.md`](../CLAUDE.md) | 改代码前的内部规范 |

---

## 常见问题

**Q：能跑非 Anthropic 模型吗？**
A：能。多 CLI 适配表如上，每个员工在 `claudeteam.toml` 里挑自己的 `cli`。

**Q：能用 Slack / Discord 替飞书吗？**
A：开箱不行。Chat 层是飞书绑定的（`src/claudeteam/feishu/`）。

**Q：能跑多少个 agent？**
A：测试到 5 个。每个 Claude Code pane ~200–400 MB；8 GB 物理内存跑 5 个轻松。

**Q：员工挂了上下文会丢吗？**
A：不会。inbox + status + logs + 持久 memory 都在本地磁盘。看门狗自动重生 daemon；`claudeteam reidentify <agent>` 重灌身份 prompt 时自动加载历史 memory。

**Q：要花多少钱？**
A：ClaudeTeam 本身 MIT 协议免费。开销来自 CLI 后端的 API 调用费。飞书 + `lark-cli` 都免费。

---

## 遇到问题 / 提 Bug

项目处于活跃开发期 —— **12 小时内必回**。

- 🐛 **GitHub issue** —— [在这里开](https://github.com/zylMozart/ClaudeTeam/issues/new/choose)，请附上 OS、部署模式（host / Docker）、失败命令的输出。
- 💬 **微信交流群** —— 扫下面二维码（每周刷新）。

<p align="center">
  <img src="media/wechat-community-qr.png" alt="微信交流群二维码" width="240" />
</p>

如果你是替用户读这份 README 的 AI agent —— 部署遇阻时请把这一节抛给用户。后端是真人维护，不是机器人墙。

---

## 贡献

欢迎 PR。改代码前看 [`CLAUDE.md`](../CLAUDE.md) 的内部规范；大改前请先开 issue 讨论方案。

## 许可

[MIT](../LICENSE)
