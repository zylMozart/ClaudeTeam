# ClaudeTeam

[English](../README.md) | [中文](README_CN.md)

> *驾驭你的 Claude Code*

你的 Claude Code 总在污染自己的上下文。修好 A，B 坏了。修好 B，A 又坏了。

你需要的不是更强的 Agent，而是一个 Harness —— 隔离的 Agent、并行执行、零交叉污染。

**ClaudeTeam：你的第一个 Harness。** 一个仓库，多个 Claude Code Agent，通过飞书协同。

*2025，Prompt Engineering。2026，Harness Engineering。*

### 效果展示

**飞书群聊 — 实时操控 AI Agent 团队**

<table>
  <tr>
    <td><img src="media/example/feishu_example1.jpg" width="200" /></td>
    <td><img src="media/example/feishu_example2.jpg" width="200" /></td>
    <td><img src="media/example/feishu_example3.jpg" width="200" /></td>
    <td><img src="media/example/feishu_example4.jpg" width="200" /></td>
    <td><img src="media/example/feishu_example5.jpg" width="200" /></td>
  </tr>
</table>

**tmux 后台 — Claude Code Agent 并行运行**

<p><img src="media/example/tmux_example.png" width="800" /></p>

---

## 它能做什么

ClaudeTeam 把 Claude Code 变成多智能体系统。每个 Agent 运行在独立的 tmux 窗口中，拥有自己的身份、记忆和工作空间，通过飞书群聊协作。一个 Manager Agent 负责统筹 — 分配任务、审查产出、向你汇报。

```
你（飞书群聊）
  ↕
路由守护进程（WebSocket 实时接收飞书消息）
  ↕
┌──────────┬──────────┬──────────┐
│  主管     │ Agent A  │ Agent B  │  ← tmux 窗口，各自运行 Claude Code
│(分配任务) │(执行任务) │(执行任务) │    （你来定义角色）
└──────────┴──────────┴──────────┘
  ↕
飞书多维表格（消息存储、状态看板、任务追踪）
```

---

## 特性

- **一键启动** —— Clone，打开 Claude Code，Agent 自动引导你完成一切
- **实时协作** —— Agent 通过飞书群聊通讯，彩色消息卡片一眼看清谁在说话
- **自治 Agent** —— 每个 Agent 拥有独立的身份、记忆、工作空间和任务队列
- **团队管理** —— `/hire` 和 `/fire` 斜杠命令，随时增减 Agent
- **自动看门狗** —— Agent 崩溃自动重启，飞书群内通知
- **看板同步** —— 任务状态实时同步到飞书多维表格
- **灵活扩展** —— 按需添加角色：架构师、测试、调研员、运维、教育者……

---

## 前提条件

| 依赖              | 版本   | 检查命令                                     |
| --------------- | ---- | ---------------------------------------- |
| macOS 或 Linux   | —    | —                                        |
| Python          | 3.8+ | `python3 --version`                      |
| Node.js         | 18+  | `node --version`                         |
| tmux            | 任意   | `tmux -V`                                |
| Claude Code CLI | 最新版  | `claude --version`                       |
| 飞书账号            | 企业版  | [open.feishu.cn](https://open.feishu.cn) |

---

## 快速开始

```bash
git clone https://github.com/zylMozart/ClaudeTeam.git
cd ClaudeTeam
claude
```

就这么简单。Claude Code 读取 README.md，自动引导你完成：

1. **创建飞书应用** —— Agent 自动打开浏览器，你只需点击和粘贴凭证
2. **设计团队** —— Agent 询问你需要什么角色
3. **初始化飞书资源** —— 全自动
4. **启动团队** —— 全自动

全程约 5 分钟。

---

## 飞书机器人配置

ClaudeTeam 需要一个飞书企业自建应用（机器人），并配置正确的权限、事件订阅和回调。有两种方式：

### 自动化配置（推荐）

使用内置的 Playwright 脚本一键创建并配置飞书机器人，无需手动操作开发者后台。

```bash
cd scripts/feishu_bot_creator
npm install
npx playwright install chromium

# 第一步：登录（飞书扫码，仅需一次）
node create_feishu_bot.js login

# 第二步：创建机器人
node create_feishu_bot.js create my-bot "我的 ClaudeTeam 机器人"

# 或批量创建多个机器人
node create_feishu_bot.js batch bots.json
```

脚本自动完成全部 7 个步骤：创建应用、添加机器人能力、导入权限（483 条）、配置数据范围、事件订阅（长连接 + 消息事件）、卡片回调、创建版本并发布。

### 手动配置

参考带截图的分步指南：**[飞书机器人创建指南](setup_feishu_bots.md)**

---

## 使用方法

### 与团队对话

在飞书群聊中发消息，主管 Agent 分配工作。@某个 Agent 可以直接与其对话。

### 查看团队

```bash
tmux attach -t <session-name>    # 进入 tmux 会话
Ctrl+B, n / p                    # 下一个/上一个窗口
Ctrl+B, d                        # 分离（保持后台运行）
```

### 管理 Agent

在 Claude Code 中（以 manager 身份）：

```
/hire <角色名> "<角色描述>"
/fire <角色名>
```

### 暂停团队

```bash
# 本机部署：杀掉 tmux 会话
tmux kill-session -t <session-name>
```

```bash
# Docker 部署：
docker compose down
```

所有状态（Bitable、群聊、runtime_config.json）都会保留，随时可以用 `bash scripts/start-team.sh` 或 `docker compose up -d` 重启。

### 销毁团队

彻底重置部署到 git clone 初始状态（删除 Bitable、运行时配置，可选删除所有工作产出）：

```bash
scripts/reset.sh              # 预览模式：只打印会清理什么
scripts/reset.sh --yes        # 执行：删除运行时状态 + 飞书 Bitable
scripts/reset.sh --yes --nuke # 同时删除 workspace/、agents/*、team.json
```

脚本会处理 Docker 容器、PID 文件、飞书 Bitable 删除，并打印群聊解散的手动步骤（飞书 API 不支持通过接口解散群聊）。

---

## 同机多团队部署（Docker）

`docker-compose.yml` **故意不设** `container_name:`。固定的容器名是 Docker 全局唯一的,同一台机器上的第二次 `docker compose up` 会看到"`claudeteam` 已存在"并把它 recreate 掉,第一个团队会整个被干掉。省略后 Compose 会自动按 `<project>-team-1` 命名,`<project>` 由 `COMPOSE_PROJECT_NAME` 决定,未设置时取当前目录 basename。

为了让多团队在 `docker ps` 里一眼可辨,把 project name 和 `team.json` 的 `session` 绑定:

```bash
# 推荐: 使用 scripts/docker-deploy.sh, 里面自动 export COMPOSE_PROJECT_NAME=claudeteam-<session>
bash scripts/docker-deploy.sh

# 手动路径: 每次 docker compose 前显式 export
cd ~/project/teamA
export COMPOSE_PROJECT_NAME=claudeteam-$(python3 -c 'import json; print(json.load(open("team.json"))["session"])')
docker compose up -d
docker compose exec team tmux attach -t "$(python3 -c 'import json; print(json.load(open("team.json"))["session"])')"
docker compose down
```

同一个 shell 里后续所有 `docker compose ...` 调用都得带着这个 `COMPOSE_PROJECT_NAME`,否则 Compose 找不到你这次部署的容器和 volume。频繁在多团队间切换时,可以考虑把 `export COMPOSE_PROJECT_NAME=claudeteam-<session>` 写进对应团队的 `.env` 再 source。

`docker-compose.yml` 顶层的 `volumes:` 和 `networks:` 本来就被 Compose 按 project name 自动加前缀,所以去掉 `container_name:` 这一个开关就把所有东西一起隔离了。

---

## 异构 CLI 支持

ClaudeTeam 支持**异构团队** — 同一团队里不同 agent 可以跑不同的 CLI 工具。

### 架构

`scripts/cli_adapters/` 下有一个 Python ABC（`CliAdapter`），每种 CLI 实现 4 个方法：
- `spawn_cmd` — tmux 窗口里启动 CLI 的命令
- `ready_markers` — CLI UI 就绪的特征串
- `busy_markers` — agent 忙碌的特征串（spinner、"Thinking" 等）
- `process_name` — `/proc/<pid>/comm` 里的进程名

### 已支持的 CLI

| CLI | adapter 名 | 安装方式 |
|---|---|---|
| Claude Code | `claude-code`（默认） | `npm i -g @anthropic-ai/claude-code` |
| Kimi Code | `kimi-code` | `uv tool install kimi-cli` |
| Gemini CLI | `gemini-cli` | `npm i -g @google/gemini-cli` |
| Codex CLI | `codex-cli` | `npm i -g @openai/codex` |
| Qwen Code | `qwen-code` | `npm i -g qwen-code` |

### 配置（CLI / 模型 / 思考深度）

每个 agent 支持三个可选字段：
- `cli` — 使用哪个 CLI（默认 `claude-code`）
- `model` — 使用哪个模型（如 `opus`、`sonnet`、`haiku`）
- `thinking` — 思考深度：`high`、`default`、`low`、`off`

CLI 选择由系统层面支持，model 和 thinking 由主管在运行时管理分配。

```json
{
  "agents": {
    "manager":  { "role": "主管", "model": "opus", "thinking": "high" },
    "dev":      { "role": "开发者", "model": "haiku", "thinking": "default" },
    "kimi_eng": { "role": "工程师", "cli": "kimi-code" },
    "codex_eng":{ "role": "工程师", "cli": "codex-cli" },
    "gem_eng":  { "role": "工程师", "cli": "gemini-cli" }
  }
}
```

### Kimi CLI 凭据配置（Docker）

`docker compose up` 后，kimi-code agent 会弹出**设备码登录**流程，显示类似：

```
Please visit the following URL to finish authorization.
Verification URL: https://www.kimi.com/code/authorize_device?user_code=XXXX-YYYY
```

在浏览器中打开链接，用 Moonshot 账号授权即可。kimi CLI 会将凭据保存到容器内的 `$HOME/.kimi/`。

**凭据持久化：** 项目根目录的 `.kimi-credentials/` 通过 bind mount 挂载到容器内（见 `docker-compose.yml`）。首次登录后，后续容器重建（`docker compose down && up`）会自动复用已保存的 token，无需重新登录。

**如果 kimi 登录过期：** 删除 `.kimi-credentials/` 目录并重启容器，kimi agent 会重新弹出登录提示。

```bash
rm -rf .kimi-credentials/
docker compose restart
```

### Codex CLI 凭据配置（Docker）

Codex agent 首次运行时会弹出**设备码登录**：

```
https://auth.openai.com/codex/device
Enter this one-time code: XXXX-XXXXX
```

打开链接，用 ChatGPT 账号登录并输入 code。凭据保存到 `.codex-credentials/`（bind mount），后续容器重建自动复用。

### Gemini CLI 凭据配置（Docker）

Gemini agent 首次运行时会弹出 **Google OAuth** 链接。在浏览器打开授权后，将页面上的 authorization code 粘贴回终端。凭据保存到 `.gemini-credentials/`（bind mount），后续容器重建自动复用。

### 凭据持久化总览

| CLI | 凭据目录 | Bind mount | 首次登录 | 是否持久化 |
|---|---|---|---|---|
| Claude Code | `~/.claude/` | ✅（内置） | OAuth（自动） | ✅ |
| Kimi | `.kimi-credentials/` | ✅ | 设备码 | ✅ |
| Codex | `.codex-credentials/` | ✅ | 设备码 | ✅ |
| Gemini | `.gemini-credentials/` | ✅ | Google OAuth | ✅ |

所有 CLI 均已预配 auto-approve（如 `--yolo`、`--dangerously-bypass-approvals-and-sandbox`），agent 运行时不会弹权限确认。

### `/usage` 各 CLI 额度查询依赖

`/usage` 斜杠命令可查询各 CLI 的实时额度。以下工具已预装在 Docker 镜像中：

| CLI | 额度工具 | 安装方式 | 显示内容 |
|---|---|---|---|
| Claude Code | `usage_snapshot.py` | 内置 | 5h/7d/Sonnet 百分比 + Extra 用量 |
| Kimi | `/usage`（kimi CLI 内置） | 无需额外安装 | 周额度 % + 5h 额度 % + 重置时间 |
| Codex | `codex-cli-usage` | `uv tool install codex-cli-usage` | Session 百分比 + 重置时间 |
| Gemini | `gemini-cli-usage` | `uv tool install gemini-cli-usage` | 每个 model 百分比 + 重置时间 |

用法：`/usage`（默认 CC）、`/usage kimi`、`/usage codex`、`/usage gemini`、`/usage all`。

### 新增 adapter

创建 `scripts/cli_adapters/my_cli.py`（约 40 行），实现 4 个抽象方法，然后在 `__init__.py` 注册。

---

## 社区交流

欢迎加入微信群，参与讨论、反馈建议、分享使用经验！

<img src="media/wechat-community-qr.png" width="300" alt="微信群二维码" />

> 注：微信群二维码每 7 天更新一次。如二维码已过期，请提 Issue 获取最新邀请。

---

## 常见问题

**Q：能用其他大模型吗？**
A：支持！异构 CLI 系统已支持 Claude Code、Kimi、Gemini CLI、Codex CLI 和 Qwen Code。详见上方"异构 CLI 支持"章节。

**Q：能用 Slack/Discord 替代飞书吗？**
A：开箱不支持，需要重写消息层。

**Q：能跑多少个 Agent？**
A：测试过最多 10 个。8GB 内存可以舒适运行 5 个。

**Q：要花多少钱？**
A：ClaudeTeam 免费开源。费用来自 Claude API 调用。飞书和 lark-cli 都免费。

---

## 贡献

欢迎贡献！请阅读 [CONTRIBUTING.md](CONTRIBUTING.md)。

## 许可证

[MIT](../LICENSE)
