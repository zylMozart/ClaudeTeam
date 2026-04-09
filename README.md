# ClaudeTeam

[English](docs/README_EN.md) | [中文](README.md)

> *Harness Your Claude Code*

你的 Claude Code 总在污染自己的上下文。修好 A，B 坏了。修好 B，A 又坏了。

你需要的不是更强的 Agent，而是一个 Harness —— 隔离的 Agent、并行执行、零交叉污染。

**ClaudeTeam：你的第一个 Harness。** 一个仓库，多个 Claude Code Agent，通过飞书协同。

*2025，Prompt Engineering。2026，Harness Engineering。*

### 效果展示

**飞书群聊 — 实时操控 AI Agent 团队**

<table>
  <tr>
    <td><img src="docs/media/example/feishu_example1.jpg" width="200" /></td>
    <td><img src="docs/media/example/feishu_example2.jpg" width="200" /></td>
    <td><img src="docs/media/example/feishu_example3.jpg" width="200" /></td>
    <td><img src="docs/media/example/feishu_example4.jpg" width="200" /></td>
    <td><img src="docs/media/example/feishu_example5.jpg" width="200" /></td>
  </tr>
</table>

**tmux 后台 — Claude Code Agent 并行运行**

<p><img src="docs/media/example/tmux_example.png" width="800" /></p>

---

## 它能做什么

ClaudeTeam 把 Claude Code 变成多智能体系统。每个 Agent 运行在独立的 tmux 窗口中，拥有自己的身份、记忆和工作空间，通过飞书群聊与队友协作。一个 Manager Agent 负责统筹全局 —— 分配任务、审查产出、向你汇报。

**工作原理：**

```
你（飞书群聊）
  ↕
路由守护进程（轮询飞书，分发消息）
  ↕
┌──────────┬──────────┬──────────┐
│  主管     │ Agent A  │ Agent B  │  ← tmux 窗口，各自运行 Claude Code
│(分配任务) │(执行任务) │(执行任务) │    （你来定义角色）
└──────────┴──────────┴──────────┘
  ↕
飞书多维表格（消息存储、状态看板、任务追踪）
```

你在飞书群里发消息，主管分配工作，Agent 执行、协作、汇报。所有消息记录在飞书多维表格中，全程可追溯。

---

## 特性

- **一键启动** —— Clone 仓库、运行 `setup.py`，CLAUDE.md 自动生成供 Claude Code 读取
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
| tmux            | 任意   | `tmux -V`                                |
| Claude Code CLI | 最新版  | `claude --version`                       |
| 飞书账号            | 企业版  | [open.feishu.cn](https://open.feishu.cn) |


安装 Claude Code：

```bash
npm install -g @anthropic-ai/claude-code
```

---

## 快速开始（30 秒）

```bash
git clone https://github.com/zylMozart/ClaudeTeam.git
cd ClaudeTeam
claude
```

就这么简单。初始化会自动生成 `CLAUDE.md`，Claude Code 启动时读取它：

1. **飞书凭证** —— 在飞书开放平台创建应用，粘贴 App ID 和 App Secret
2. **设计团队** —— 定义你的团队角色（manager + 任何你需要的角色）
3. **自动初始化** —— 创建飞书群组、多维表格、Agent 目录
4. **启动** —— 在 tmux 中启动所有 Agent，开始运行

全程约 5 分钟，大部分时间花在创建飞书应用上。

---

## 手动设置（如果你更喜欢）

点击展开手动设置步骤

### 1. 配置飞书凭证

```bash
cp .env.example .env
# 编辑 .env，填入飞书 App ID 和 App Secret
```

**获取飞书凭证：**

1. 访问 [飞书开放平台](https://open.feishu.cn) → 开发者后台
2. 创建企业自建应用
3. 在「凭证与基础信息」页面复制 App ID 和 App Secret
4. 添加必选权限：
  - `bitable:app`（多维表格）
  - `im:chat`（群组管理）
  - `im:message`（消息收发）
  - `im:resource`（文件上传下载）
5. （可选）如需文档同步功能，还需添加：
  - `docx:document`（文档读写）
  - `drive:drive`（云空间管理）
6. 发布应用

### 2. 定义团队

在项目根目录创建 `team.json`。每个团队必须包含 `manager`，其他角色自行定义。参考 `templates/` 目录下的身份模板。

```json
{
  "session": "my-team",
  "agents": {
    "manager": {"role": "主管", "emoji": "🎯", "color": "blue"}
  }
}
```

### 3. 安装依赖并初始化

```bash
pip install -r requirements.txt
python3 scripts/setup.py
```

### 4. 启动

```bash
bash scripts/start-team.sh
```

---

## 使用方法

### 与团队对话

在飞书群聊中发消息，主管 Agent 会阅读并分配工作。你可以 @某个 Agent 直接与其对话。

### 查看团队

```bash
# 进入 tmux 会话
tmux attach -t <session-name>

# 切换 Agent 窗口
Ctrl+B, n     # 下一个窗口
Ctrl+B, p     # 上一个窗口
Ctrl+B, 0-9   # 按编号跳转

# 分离（保持后台运行）
Ctrl+B, d
```

### 管理 Agent

在 Claude Code 中（以 manager 身份）：

```
/hire <角色名> "<角色描述>"
/fire <角色名>
```

### 通讯命令

所有 Agent 使用 `feishu_msg.py` 通讯：

```bash
# 发送私信
python3 scripts/feishu_msg.py send <收件人> <发件人> "<消息>" [高|中|低]

# 群聊发言
python3 scripts/feishu_msg.py say <名字> "<消息>"

# 查看收件箱
python3 scripts/feishu_msg.py inbox <名字>

# 更新状态
python3 scripts/feishu_msg.py status <名字> <状态> "<描述>"

# 记录日志
python3 scripts/feishu_msg.py log <名字> 任务日志 "<做了什么>"
```

---

## 团队自定义

每个团队必须包含一个 **manager** Agent。除此之外，你可以定义任何你需要的角色 —— 没有固定模板。在引导式设置中，Claude 会询问你需要哪些角色。

使用 `/hire` 和 `/fire` 随时增减 Agent。参考 `templates/` 目录下的身份模板了解角色定义方式。

---

## 项目结构

```
ClaudeTeam/
├── README.md                  # 本文件（中文文档）
├── LICENSE                    # MIT 许可证
├── .env.example               # 凭证模板
├── requirements.txt           # Python 依赖
│
├── docs/                      # 文档
│   ├── POLICY.md              # 团队通讯规范
│   ├── README_EN.md           # English documentation
│   └── CONTRIBUTING.md        # 贡献指南
│
├── scripts/                   # 运行时脚本
│   ├── config.py              # 配置加载器
│   ├── setup.py               # 一键初始化
│   ├── start-team.sh          # 团队启动器
│   ├── feishu_msg.py          # 消息总线
│   ├── feishu_router.py       # 消息路由守护进程
│   ├── tmux_utils.py          # tmux 工具
│   ├── token_cache.py         # 飞书 Token 管理
│   ├── hire_agent.py          # Agent 招聘助手
│   ├── fire_agent.py          # Agent 裁撤助手
│   ├── watchdog.py            # 进程监控
│   ├── memory_manager.py      # Agent 记忆管理
│   ├── kanban_sync.py         # 看板同步
│   ├── task_tracker.py        # 任务追踪
│   ├── feishu_sync.py         # 文件同步到飞书文档（可选）
│   └── upload_folded_doc.py   # Markdown 上传飞书文档（可选）
│
├── templates/                 # 身份模板
│   ├── manager.identity.md    # 主管角色模板
│   └── worker.identity.md     # 通用员工模板
│
└── .claude/skills/            # 斜杠命令
    ├── hire/SKILL.md           # /hire 命令
    └── fire/SKILL.md           # /fire 命令
```

**运行时生成（已 gitignore）：** `.env`、`team.json`、`CLAUDE.md`、`agents/`、`workspace/`、`scripts/runtime_config.json`

---

## 工作原理（架构）

### 消息流

1. **用户** 在飞书群聊发消息
2. **路由器**（`feishu_router.py`）每 3 秒轮询群消息，检测新消息
3. 路由器解析 @提及，将消息注入目标 Agent 的 tmux 窗口
4. **Agent**（Claude Code）处理消息，执行任务
5. Agent 使用 `feishu_msg.py` 回复 —— 消息同时出现在飞书群和多维表格中

### 基础设施


| 组件       | 脚本                 | 用途                         |
| -------- | ------------------ | -------------------------- |
| 消息总线     | `feishu_msg.py`    | 收发消息、更新状态、记录日志             |
| 路由器      | `feishu_router.py` | 轮询飞书群 → 通过 tmux 分发给 Agent  |
| 看门狗      | `watchdog.py`      | 监控进程，崩溃自动重启                |
| 看板       | `kanban_sync.py`   | 任务状态同步到飞书多维表格              |
| Token 缓存 | `token_cache.py`   | 缓存飞书 API Token（1.5 小时 TTL） |


### Agent 生命周期

```
/hire → 创建目录 → 生成 identity.md → 创建多维表格
     → 打开 tmux 窗口 → 启动 Claude Code → 发送初始化消息
     → Agent 读取身份 → 查收件箱 → 开始工作

/fire → 归档工作空间 → 关闭 tmux 窗口 → 从 team.json 移除
     → 清理飞书资源
```

---

## 常见问题

**Q：支持其他大模型吗？**
A：目前 ClaudeTeam 专为 Claude Code 构建。Agent 外壳（tmux 管理、消息路由、身份系统）理论上可以适配其他 CLI 大模型工具，但尚未测试。

**Q：能用 Slack/Discord 替代飞书吗？**
A：开箱不支持。消息层（`feishu_msg.py`）是飞书专用的，替换平台需要重写消息总线和路由器。

**Q：能跑多少个 Agent？**
A：单机测试过最多 10 个。每个 Agent 是一个 Claude Code 进程，资源消耗线性增长。8GB 内存可以舒适运行 5 个 Agent。

**Q：`--dangerously-skip-permissions` 安全吗？**
A：该标志允许 Agent 无需手动审批就执行命令，这是自治运行的必要条件。请仅在可信环境中使用，谨慎评估分配的任务。

**Q：Agent 崩溃了怎么办？**
A：看门狗会监控所有 Agent 进程并自动重启，飞书群内会收到通知。

**Q：能停止后再恢复吗？**
A：可以。tmux 分离（`Ctrl+B, d`）后 Agent 保持后台运行。完全停止：`tmux kill-session -t <session-name>`。恢复：`bash scripts/start-team.sh`。

**Q：要花多少钱？**
A：ClaudeTeam 本身免费开源。费用来自 Claude API 调用（每个 Agent 会消耗 API 额度）和飞书（免费版足够大多数团队使用）。

---

## 贡献

欢迎贡献！请阅读 [CONTRIBUTING.md](CONTRIBUTING.md) 了解提交 Issue 和 Pull Request 的规范。

---

## 许可证

[MIT](LICENSE) —— 随便用。