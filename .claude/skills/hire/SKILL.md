---
name: hire
description: "招聘新 Agent 加入团队。用法：/hire <角色名> <角色描述>"
---

# 招聘新 Agent

招聘 $ARGUMENTS 加入团队。

从参数中解析：第一个词为角色名，其余部分为角色描述。

请严格按以下步骤执行：

## 前置检查

1. 读取 team.json，检查角色名是否已存在：
   - 若已存在且 agents/<角色名>/ 目录存在 → 报错退出："该员工已在职，无需重复招聘"
   - 若 team.json 无条目但 agents/_archived/ 下有匹配目录（格式 `<角色名>_*`）→ 提示"该员工曾被裁撤，正在重新聘用"，将最新的归档目录恢复到 agents/<角色名>/
   - 若 team.json 有条目但 agents/<角色名>/ 不存在（脏数据）→ 清理 team.json 条目，按全新招聘处理
   - 若不存在 → 继续

## 步骤 1：更新 team.json

读取 team.json，在 agents 对象中添加新条目：
```json
{
  "<角色名>": {"role": "<根据描述生成的中文角色名>", "emoji": "<选择合适的emoji>", "color": "<飞书卡片颜色>"}
}
```

**`color` 必填**,从下列飞书卡片 header template 颜色里选一个**团队现有 agent 还没用过**的,确保每个成员在群聊卡片上视觉可区分:

```
blue, turquoise, orange, purple, green, carmine, indigo, violet, wathet, yellow, red
```

选色原则:
- 先扫一遍 team.json 里已用的 color,排除掉
- 按上面列出的顺序从前往后挑第一个未用过的
- 如果想体现角色语义也可以(比如 tester→red、designer→wathet),但必须和现有颜色不同
- 不要用 `grey`(那是 fallback 色,表示未分配)

写回 team.json。

> 兜底: 如果你忘了写 color,`hire_agent.py setup-feishu` 会自动补一个,但尽量在这一步直接写对,避免文件被重复修改。

## 步骤 2：创建目录结构

创建以下目录和文件：
- `agents/<角色名>/memory/`
- `agents/<角色名>/memory/archive/`
- `agents/<角色名>/workspace/`
- `agents/<角色名>/tasks/`

## 步骤 3：生成 identity.md

根据角色描述，生成标准格式的 `agents/<角色名>/identity.md`，内容包括：
- 角色定义
- 职责列表
- 通讯规范（套用以下模板，替换角色名）
- 工作流
- 产出规范

通讯规范模板（必须包含）：
```
## 通讯规范（必须遵守）
```bash
# 查看收件箱（启动后第一件事）
python3 scripts/feishu_msg.py inbox <角色名>

# 向 manager 汇报
python3 scripts/feishu_msg.py send manager <角色名> "<消息>" 高

# 更新自己状态
python3 scripts/feishu_msg.py status <角色名> 进行中 "<当前在做什么>"

# 记录工作日志
python3 scripts/feishu_msg.py log <角色名> 任务日志 "<做了什么>"
```
```

## 步骤 4：生成 core_memory.md

创建 `agents/<角色名>/core_memory.md`，标准骨架：
```markdown
# <角色名> 核心记忆

> 最后更新：<当前时间 YYYY-MM-DD HH:MM>

## 关键事实
- 入职时间：<当前日期>
- 角色：<角色描述>

## 当前状态
- 刚入职，等待初始化

## 扩展记忆索引
- （按需添加）
```

## 步骤 5：创建飞书工作空间表

执行：
```bash
python3 scripts/hire_agent.py setup-feishu <角色名>
```

## 步骤 6：启动 tmux 窗口

执行：
```bash
python3 scripts/hire_agent.py start-tmux <角色名>
```

## 步骤 7：通知团队

执行：
```bash
python3 scripts/feishu_msg.py say manager "欢迎新同事 <角色名> 加入团队！角色：<中文角色名>，职责：<简短描述>"
```

## 完成

向 manager 汇报招聘结果。
