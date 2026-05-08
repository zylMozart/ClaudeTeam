---
name: task-workflow
description: "Task lifecycle workflow using task_tracker.py plus status/log reporting."
---

# Task Workflow

Use this skill when creating, updating, or closing a ClaudeTeam task.

## Boundary

`task_tracker.py` is the task fact wrapper. `feishu_msg.py status` is the agent
self-report wrapper. A task is not complete for manager until both the task fact
and manager report are consistent.

## Commands

```bash
# List tasks
python3 scripts/task_tracker.py list
python3 scripts/task_tracker.py list --assignee <agent>
python3 scripts/task_tracker.py list --status <状态>

# Inspect one task
python3 scripts/task_tracker.py get <TASK-NNN>

# Create a task
python3 scripts/task_tracker.py create <assignee> "<title>" "<description>" --by <creator>

# Update a task
python3 scripts/task_tracker.py update <TASK-NNN> --status <状态>
python3 scripts/task_tracker.py update <TASK-NNN> --assignee <agent>
python3 scripts/task_tracker.py update <TASK-NNN> --title "<new title>"
```

Known task statuses:

- `待处理`
- `进行中`
- `已完成`
- `已取消`

## Procedure

1. Read the task with `get <TASK-NNN>` before editing.
2. Set the task to `进行中` when active work starts.
3. Keep agent status aligned with the current task:

```bash
python3 scripts/feishu_msg.py status <agent> 进行中 "<TASK-NNN: short current action>"
```

4. Record important evidence with `feishu_msg.py log`.
5. When finished, update the task to `已完成`.
6. Send manager the completion summary, changed files, and verification command output.

## Compatibility Notes

- Do not edit `workspace/shared/tasks/tasks.json` directly unless repairing the tracker itself.
- Do not treat `status 已完成` alone as task completion.
- Do not treat a local workspace note alone as manager notification.
