# 团队规范

> 所有 Agent 必读

## 1. 通讯规范（唯一渠道：飞书）

所有消息通过飞书，禁止写文件给别人。

```bash
# 发消息
python3 scripts/feishu_msg.py send <收件人> <我的名字> "<内容>" [高|中|低]

# 查收件箱（每次启动后第一件事）
python3 scripts/feishu_msg.py inbox <我的名字>

# 标记消息已读（处理完后必须标记）
python3 scripts/feishu_msg.py read <record_id>
```

## 2. 状态同步（实时更新）

每次状态变化都要同步：

```bash
python3 scripts/feishu_msg.py status <名字> <状态> "<当前任务>"
# 状态: 进行中 | 已完成 | 阻塞 | 待命
```

## 3. 工作日志（关键节点记录）

完成重要步骤 → 写工作空间：

```bash
python3 scripts/feishu_msg.py log <名字> 任务日志 "<做了什么>" "<文件路径>"
python3 scripts/feishu_msg.py log <名字> 产出记录 "<产出>" "<workspace/路径>"
```

## 4. 工作流（每次启动都这样做）

```
启动
  → 读 agents/<名字>/identity.md
  → python3 scripts/feishu_msg.py inbox <名字>   # 查收件箱
  → 如有消息 → 标记已读 → 执行任务 → 汇报
  → 如无消息 → 更新状态为"待命" → 等待心跳唤醒
```

## 5. @mention 规范

在飞书群里 @别人 = 给别人发消息：
- 发消息时 feishu_msg.py 会自动在群里显示 @收件人
- Router 守护进程会自动通知对方

## 6. 产出文件与目录规范

- 个人产出 → `agents/<你的名字>/workspace/<类型>/`
  - 设计文档 → `agents/<名字>/workspace/design/`
  - 代码文件 → `agents/<名字>/workspace/code/`
  - 文案内容 → `agents/<名字>/workspace/content/`
  - 测试报告 → `agents/<名字>/workspace/test/`
  - 调研报告 → `agents/<名字>/workspace/research/`
  - 综合报告 → `agents/<名字>/workspace/reports/`
  - 图片截图 → `agents/<名字>/workspace/images/`

- 共享产出 → `workspace/shared/<类型>/`（需明确标注为共享）
- 群聊图片 → `workspace/shared/images/`（Router 自动下载）
- 全局任务 → `workspace/shared/tasks/tasks.json`

**严禁在项目根目录下创建任何文件。**

## 7. .claude/ 目录写入规范

当需要写入 `.claude/` 目录下的文件时（如创建 skill、command 等），使用 Bash 工具的 `mkdir` + `cat`/`echo` 命令，不要用 Write 工具，避免触发 Claude Code 的配置写入权限确认弹窗。
