---
name: _template
description: "Template for new ClaudeTeam skills. Copy this directory and replace placeholders."
---

# <Skill Name>

Use this skill when <trigger condition>.

## Inputs

- `$ARGUMENTS`: <expected argument format>
- Required files: <paths to read before acting>

## Boundaries

- Read-only commands: <commands that only inspect state>
- Write-producing commands: <commands that modify files, Feishu, tmux, or tasks>
- Do not modify: <files or systems outside this workflow>

## Procedure

1. Parse the request and validate required arguments.
2. Read the local context listed above.
3. Run the smallest stable `scripts/` wrapper that performs the required action.
4. Verify the result with a read-only command or file check.
5. Report the outcome to manager when the workflow changes shared state.

## Verification

Run:

```bash
python3 tests/static_skill_layout_check.py
```

## Report

Use:

```bash
python3 scripts/feishu_msg.py send manager <agent> "<summary>" 高
```
