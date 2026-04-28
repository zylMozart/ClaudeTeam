# Boss Todo Bitable

Boss todos are blocking actions that only the user/boss can complete: OAuth login, credential handoff, approval, PR publishing, external billing, or explicit confirmation. They are stored in a dedicated Feishu Bitable table named `老板代办`; they are not written to `task_tracker.py` or employee task files.

## Setup

New `setup.py` runs create or reuse this table automatically and writes:

```json
{
  "boss_todo": {
    "base_token": "<same as bitable_app_token>",
    "table_id": "tbl...",
    "table_name": "老板代办",
    "view_link": "",
    "dedupe_keys": ["来源任务", "标题"]
  }
}
```

For an existing deployment whose `runtime_config.json` predates this table, run:

```bash
python3 scripts/setup.py ensure-boss-todo
```

The runtime reader also accepts the legacy flat keys `boss_todo_table_id`, `boss_todo_link`, and `boss_todo_dedupe_keys`. If no table ID is configured, `scripts/boss_todo.py` fails loudly and tells you to run `ensure-boss-todo` or ask devops to write the config.

## Common commands

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
