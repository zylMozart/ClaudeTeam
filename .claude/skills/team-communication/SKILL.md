---
name: team-communication
description: "Team communication workflow: inbox, read, send, say, status, and log via feishu_msg.py."
---

# Team Communication

Use this skill when an agent needs to receive, acknowledge, report, or record
team work through the standard ClaudeTeam message channel.

## Boundary

This skill documents the SOP. It does not replace the runtime delivery layer.
All actual communication still uses the stable wrapper:

```bash
python3 scripts/feishu_msg.py <command> ...
```

## Common Commands

```bash
# Check unread messages
python3 scripts/feishu_msg.py inbox <agent>

# Mark a handled message as read
python3 scripts/feishu_msg.py read <record_id>

# Send a work report or handoff to manager
python3 scripts/feishu_msg.py send manager <agent> "<message>" 高

# Speak in the group chat
python3 scripts/feishu_msg.py say <agent> "<message>"

# Update current state
python3 scripts/feishu_msg.py status <agent> <状态> "<current task>" ["<blocker>"]

# Write an audit/workspace log
python3 scripts/feishu_msg.py log <agent> 任务日志 "<what changed>" "<ref>"
```

Allowed status values:

- `进行中`
- `已完成`
- `阻塞`
- `待命`

## Procedure

1. Run `inbox <agent>` before starting assigned work.
2. Pick the newest explicit manager task unless a later user message overrides it.
3. Mark only messages that have been handled with `read <record_id>`.
4. For work that takes more than a short read-only check, set status to `进行中`.
5. When finished, send manager a concise result with evidence and test output.
6. If blocked, set status to `阻塞` and include the specific blocker.

## Compatibility Notes

- Keep `python3 scripts/feishu_msg.py ...` as the public command surface.
- Do not call internal Python functions from agent instructions.
- Do not depend on Bitable table IDs or lark profile names in the skill.
- Delivery failures must be reported as outcomes, not hidden behind "sent".
