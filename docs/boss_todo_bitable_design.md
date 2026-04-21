# 老板代办 Bitable 持久化接入方案

更新时间：2026-04-22 北京时间

## 产品规则

老板代办用于记录“必须由老板完成或确认”的阻塞项，不是员工任务单。

必须创建或 upsert 老板代办的场景：

- 需要老板审核、确认、选择方案、回复问题。
- 需要老板登录 OAuth、提供凭证、授权权限、导入 scopes、发布应用。
- 团队已汇报完成/阻塞，但下一步等待老板动作。
- PR/部署/付款/外部系统操作只能由老板执行。

标记完成的场景：

- 老板明确回复已完成、已确认、已提供、已登录。
- 员工验证老板动作已生效，例如凭证可用、权限已发布、PR 已授权。
- manager 派员工复核后，由员工或 manager 指派员工执行 `done`。

避免重复：

- 幂等键建议为 `source_task + title_normalized`。
- 同一来源任务同一标题已有 `待处理/进行中/阻塞` 时更新原记录，不新建。
- 老板动作变化时追加 `最新备注/更新时间/关联消息`，不要创建近似重复标题。

## 推荐表字段

兼容 devops 后续创建 Bitable，字段名尽量稳定：

| 字段 | 类型建议 | 说明 |
|---|---|---|
| 标题 | text | 老板需要做什么 |
| 状态 | single_select/text | 待处理、进行中、已完成、已取消 |
| 优先级 | single_select/text | 高、中、低 |
| 来源任务 | text | task_id、PR、incident、manual 等 |
| 来源类型 | text | credential、review、approval、login、reply、deploy、other |
| 创建人 | text | manager/toolsmith/devops 等 |
| 负责人 | text | 默认 boss |
| 截止时间 | text/date | 可空 |
| 最新备注 | text | 当前卡点和下一步 |
| 关联消息 | text | 飞书 record_id、PR 链接、日志路径等 |
| 创建时间 | date_time/text | 北京时间 |
| 更新时间 | date_time/text | 北京时间 |
| 完成时间 | date_time/text | 完成时写入 |

## runtime_config 持久化字段

建议新增：

```json
{
  "boss_todo": {
    "base_token": "<默认复用 bitable_app_token>",
    "table_id": "tbl...",
    "table_name": "老板代办",
    "view_link": "https://...",
    "dedupe_keys": ["来源任务", "标题"]
  }
}
```

兼容简写也可支持：

```json
{
  "boss_todo_table_id": "tbl...",
  "boss_todo_link": "https://...",
  "boss_todo_dedupe_keys": ["来源任务", "标题"]
}
```

读取优先级：

1. `runtime_config.boss_todo.table_id`
2. `runtime_config.boss_todo_table_id`
3. setup/init 查找或创建表
4. 仍没有则脚本明确报错并提示 devops 创建表/写入配置

## 初始化流程

`scripts/setup.py` 最小改动：

1. 创建主 Bitable 后，查找表名 `老板代办`。
2. 已存在则复用 table_id。
3. 不存在则创建表和字段。
4. 将 `boss_todo` 配置写入 `scripts/runtime_config.json`。
5. setup 输出提示：老板代办表已创建/复用，并打印表链接（如 lark-cli 能返回）。

如果 devops 先手工创建表：

1. devops 把 table_id/link 写进 runtime_config。
2. setup 检测到已有配置则复用，不覆盖。

已实现的最小接口：

```bash
python3 scripts/setup.py ensure-boss-todo
```

该命令用于已有部署补齐老板代办表：读取 `bitable_app_token`，查找或创建
`老板代办` 表，写入 `runtime_config.json` 的 `boss_todo` 段。新部署正常
执行 `python3 scripts/setup.py` 时也会自动创建或复用该表。

## 脚本接口

建议新增 `scripts/boss_todo.py`，独立于 `task_tracker.py`。

命令形态：

```bash
python3 scripts/boss_todo.py create "<标题>" \
  --source-task TASK-123 \
  --source-type credential \
  --priority 高 \
  --note "需要老板完成 Gemini OAuth" \
  --link "recv... 或 https://..."

python3 scripts/boss_todo.py upsert "<标题>" \
  --source-task usage-credential-p0 \
  --source-type login \
  --priority 高 \
  --note "Codex 容器内需要重新登录"

python3 scripts/boss_todo.py list [--status 待处理]

python3 scripts/boss_todo.py done "<标题或record_id>" \
  --source-task usage-credential-p0 \
  --note "老板已完成登录，devops 验证通过"
```

行为要求：

- `create`：直接创建；若检测到同 key 未完成记录，提示改用 upsert 或自动转 upsert。
- `upsert`：按 `source_task + normalized_title` 查未完成记录；存在则更新，不存在则创建。
- `list`：列出待处理/进行中，给 manager 巡检用。
- `done`：标记已完成，写完成时间和备注。

技术复用：

- 复用 `feishu_msg.py` 的 `_lark_base_create/_lark_base_search/_lark_base_update/_lark_base_list` 模式。
- 搜索端点遇到 blocked 时，沿用 list + 客户端过滤兜底。
- 不把老板代办写入普通 `workspace/shared/tasks/tasks.json`，避免员工任务和老板动作混淆。
- 回归测试通过 `CLAUDETEAM_RUNTIME_CONFIG` 指向临时配置，并通过 `BOSS_TODO_STORE`
  使用本地 JSON mock store；验收不需要真实飞书 secret。

## manager 使用规则

- manager 识别到等待老板动作时，立即派 toolsmith/devops 执行 `boss_todo.py upsert`，不能只在群里说。
- 汇报任务阻塞时必须同时说明老板代办是否已 upsert。
- 老板完成动作后，manager 派员工验证；验证通过后标记 `done`。
- 每次正式进度汇报前，manager 可派员工 `boss_todo.py list --status 待处理` 检查是否还有老板侧阻塞。

## core_memory 短规则

老板代办规则：凡是等待老板审核/确认/回复/登录/授权/提供凭证/外部操作，或团队已汇报但等待老板处理的阻塞项，都必须派员工用 `scripts/boss_todo.py upsert` 写入老板代办 Bitable；同一 `source_task + 标题` 只更新不重复创建。老板完成后先派员工验证，再用 `boss_todo.py done` 标记已完成。manager 不能只在群里口头提醒，也不能把老板代办混进普通员工任务单。

## 是否需要 devops 的表 token/link

需要二选一：

- 最佳：setup 自动创建/复用 `老板代办` 表并写入 runtime_config，不需要手工 token/link。
- 过渡：devops 先创建 Bitable 表，提供 `table_id` 和 `view_link`，toolsmith 再接脚本读取 runtime_config。

没有 `table_id` 时，`boss_todo.py` 应 loud fail，提示先运行 setup 或让 devops 写入配置。
