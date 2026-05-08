# `team.json` schema

`team.json` 描述你这一份 ClaudeTeam 部署里有哪些 agent、用什么 CLI、跑什么模型。
新部署直接 `cp templates/team.json.example team.json`,改 `session` 名称即可。

## 顶层字段

| 字段 | 类型 | 必需 | 说明 |
|---|---|---|---|
| `session` | string | ✅ | tmux session 名 (整团唯一)。也是 `lark-cli --profile` 名,docker-compose project name 默认用它。 |
| `default_model` | string | ❌ | agent 没显式 `model` 时的兜底。常见: `sonnet` / `opus` / `claude-opus-4-7` / `claude-sonnet-4-6`。 |
| `default_thinking` | string | ❌ | agent 没显式 `thinking` 时的兜底。`default` / `high` / `ultra`。 |
| `agents` | object | ✅ | agent 名 → 配置的字典。**必须**有一个 key 叫 `manager`。 |

## `agents.<name>` 字段

| 字段 | 类型 | 必需 | 说明 |
|---|---|---|---|
| `role` | string | ✅ | 中文角色名,会在飞书消息卡片里显示。 |
| `emoji` | string | ❌ | tmux pane 标题前缀。 |
| `color` | string | ❌ | tmux 状态栏颜色: `blue` / `red` / `green` / `purple` / `orange` / `indigo` / `cyan` 等。 |
| `cli` | string | ✅ | CLI 适配器 ID。可选: `claude-code` / `codex-cli` / `kimi-code` / `gemini-cli` / `qwen-code`。 |
| `model` | string | ❌ | 该 agent 的模型。Claude 系列: `sonnet` / `opus` / `haiku` / 完整 ID `claude-opus-4-7` / `claude-sonnet-4-6` / `claude-haiku-4-5-20251001`。Codex: `gpt-5.4` 等。 |
| `thinking` | string | ❌ | 思考强度。`default` / `high` / `ultra`。仅 Claude / Codex 部分模型支持。 |

## 注意事项

- `manager` agent 必须存在,否则 router/dispatcher 拒绝启动。
- `cli` 必须跟你 docker image / 宿主机已装的 CLI 对应,否则 spawn 阶段直接报错。
- 如果你团队不需要 Codex/Kimi/Gemini,直接删对应 entry 即可,不会影响其他 agent。
- 改完 `team.json` 后:
  - host-native 模式: `bash scripts/start-team.sh` 重启即可。
  - Docker 模式: `docker compose down && docker compose up -d`。
