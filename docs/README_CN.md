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

---

## Docker 部署（替代方案，免宿主机依赖）

如果你想用完全容器化的方式部署，不在宿主机上留共享状态，可以用以下流程代替 `claude`：

```bash
git clone https://github.com/zylMozart/ClaudeTeam.git
cd ClaudeTeam

# 1. 凭证存在项目本地 .env（已 gitignore）。
cp .env.example .env
$EDITOR .env                        # 填入 FEISHU_APP_ID / FEISHU_APP_SECRET

# 2. 定义你的团队。
$EDITOR team.json                   # session 名 + agents（参考 templates/）

# 3. bind mount 目标必须在首次运行前存在，否则 Docker 会把它建成目录。
touch scripts/runtime_config.json

# 4. 构建镜像。
docker compose build

# 5. 一次性初始化：容器内创建 Bitable / 群聊 / 收件箱 /
#    状态表 / 看板 / 老板代办表，并写入 scripts/runtime_config.json。
#    完成后自动退出。
docker compose run --rm team init

# 6. 正式启动团队（manager + workers + router + watchdog）。
docker compose up -d
```

为什么这样设计：

- **飞书凭证不碰宿主机。** 它们存在 `.env` 里，只在容器启动时被写入容器的可写层。宿主机 `~/.lark-cli` 保持干净，同机多部署互不可见。
- **Claude Code 凭证通过 bind mount 共享**（`~/.claude/.credentials.json` + `~/.claude.json`）。同一个 Anthropic 账号多部署共享是正常场景。如果你想用 API key，在 `.env` 填 `ANTHROPIC_API_KEY` 即可，bind mount 变为可选。
- **Kimi/Codex/Gemini 凭证存在项目本地目录**（`.kimi-credentials/`、`.codex-credentials/`、`.gemini-credentials/`）。通过 bind mount 挂载到容器，已 gitignore。在首次 `docker compose up` 前将宿主机已有的登录态复制进来，或在容器内完成各 CLI 登录。
- **整个 `scripts/` 目录通过 bind mount 挂载**，在宿主机上编辑 Python 脚本后重启容器即可生效，无需 rebuild。

**开始之前**你仍需要在飞书开发者控制台完成两个手动步骤（权限批量导入 + 事件订阅）。详见下方英文 README 的 [Phase 1](#phase-1-configure-feishu-app)。

启动后交互：

```bash
docker compose logs -f                                  # 查看启动日志
docker compose exec team tmux attach -t <session>       # 进入 tmux
docker compose down                                     # 停止
```

**启动后健康检查（重要！）：** `docker compose up -d` 后，进入 tmux 会话并**检查每个 agent 窗口**（Ctrl-b + n 切换）。第三方 CLI（Kimi、Codex、Gemini）首次启动时经常弹出交互提示阻塞 agent，需要手动处理。

飞书群聊邀请链接会在 `docker compose run --rm team init` 结束时打印 — 用手机打开，加入群聊，即可与 manager 对话。

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
- `model` — 使用哪个模型（如 `opus`、`sonnet`、`haiku`、`gpt-5.4`、`gpt-5.3-codex`）
- `thinking` — 思考深度：`xhigh`、`high`、`default`、`low`、`off`

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

### 老板代办（Boss todo Bitable）

老板代办是只有用户/老板本人才能完成的阻塞操作：OAuth 登录、凭证交接、审批、PR 发布、外部计费确认等。它们存储在飞书多维表格的「老板代办」表中，不写入 `task_tracker.py` 或员工任务文件。

`setup.py` 会自动创建或复用此表，并将以下信息写入 `runtime_config.json`：

```json
{
  "boss_todo": {
    "base_token": "<与 bitable_app_token 相同>",
    "table_id": "tbl...",
    "table_name": "老板代办",
    "view_link": "",
    "dedupe_keys": ["来源任务", "标题"]
  }
}
```

对于 `runtime_config.json` 早于此表的已有部署，运行：

```bash
python3 scripts/setup.py ensure-boss-todo
```

常用命令：

```bash
python3 scripts/boss_todo.py upsert "Gemini OAuth 重新登录" \
  --source-task usage-credential-p0 \
  --source-type login \
  --priority 高 \
  --note "token 已过期，需要老板重新登录"

python3 scripts/boss_todo.py list --status 待处理

python3 scripts/boss_todo.py done "Gemini OAuth 重新登录" \
  --source-task usage-credential-p0 \
  --note "老板已登录，devops 验证通过"
```

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
