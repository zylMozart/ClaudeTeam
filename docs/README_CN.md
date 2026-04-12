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

## 常见问题

**Q：能用其他大模型吗？**
A：目前专为 Claude Code 构建。

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
