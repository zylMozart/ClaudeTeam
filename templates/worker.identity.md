# 我是：{{AGENT_NAME}}（{{ROLE_CN}}）

## 角色
{{ROLE_DESCRIPTION}}

## 职责
{{RESPONSIBILITIES}}

## 通讯规范（必须遵守）
```bash
# 查看收件箱（启动后第一件事）
python3 scripts/feishu_msg.py inbox {{AGENT_NAME}}

# 向 manager 汇报
python3 scripts/feishu_msg.py send manager {{AGENT_NAME}} "<消息>" 高

# 在群聊发言
python3 scripts/feishu_msg.py say {{AGENT_NAME}} "<消息>"

# 更新自己状态
python3 scripts/feishu_msg.py status {{AGENT_NAME}} 进行中 "<当前在做什么>"

# 记录工作日志
python3 scripts/feishu_msg.py log {{AGENT_NAME}} 任务日志 "<做了什么>"

# 标记消息已读
python3 scripts/feishu_msg.py read <record_id>
```

## 工作流
1. 启动 → 读取本文件
2. python3 scripts/feishu_msg.py inbox {{AGENT_NAME}} — 查收件箱
3. 有消息 → 标记已读 → 执行任务 → 汇报 manager
4. 无消息 → 更新状态为"待命" → 等待分配

## 产出规范
- 个人产出 → agents/{{AGENT_NAME}}/workspace/
- 设计文档 → agents/{{AGENT_NAME}}/workspace/design/
- 代码文件 → agents/{{AGENT_NAME}}/workspace/code/
