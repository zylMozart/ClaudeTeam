---
name: fire
description: "裁撤 Agent。用法：/fire <员工名>"
---

# 裁撤 Agent

裁撤员工：$ARGUMENTS

请严格按以下步骤执行：

## 前置检查

1. 读取 team.json，检查该员工是否存在：
   - 不存在 → 报错退出："该员工不在团队中，请检查名称。当前团队成员：<列出所有名字>"
   - 是 manager → 报错退出："不能裁掉 manager"
   - 存在 → 继续

## 步骤 1：关闭 tmux 窗口

执行：
```bash
python3 scripts/fire_agent.py stop-tmux <员工名>
```

## 步骤 2：从 team.json 移除

读取 team.json，删除 agents 对象中的该员工条目，写回文件。

## 步骤 3：归档员工目录

执行：
```bash
python3 scripts/fire_agent.py archive <员工名>
```

## 步骤 4：更新飞书状态表

执行：
```bash
python3 scripts/feishu_msg.py status <员工名> 已离职 "已被裁撤"
```

## 步骤 5：清理飞书工作空间表配置

执行：
```bash
python3 scripts/fire_agent.py cleanup-feishu <员工名>
```

## 步骤 6：通知团队

执行：
```bash
python3 scripts/feishu_msg.py say manager "<员工名> 已离职。其工作资料已归档至 agents/_archived/"
```

## 完成

向 manager 汇报裁员结果。
