# ClaudeTeam Skill Layout

This directory contains Claude Code skills for operator and agent workflows.
Skills are workflow/SOP entry points, not runtime modules. Runtime behavior must
stay in `scripts/` compatibility commands now, and later in the planned
`claudeteam` Python package.

## Boundary

Use a skill when the work needs human/agent judgment, multiple files, or a
repeatable procedure:

- hiring or removing an agent
- triaging an inbox task
- running a task/status workflow
- collecting server status evidence
- applying a documented migration checklist

Do not put core runtime logic in a skill:

- Feishu event routing
- Bitable read/write adapters
- tmux injection and queue fallback
- lifecycle wake/suspend decisions
- slash command dispatch internals

Those belong in importable Python modules with tests. Skills may call the
stable command wrappers such as `python3 scripts/feishu_msg.py inbox <agent>`.

## Required Skill Format

Each runtime skill directory must contain `SKILL.md` with YAML frontmatter:

```markdown
---
name: skill-name
description: "Short trigger description and usage."
---

# Skill Title
```

Rules:

- Directory name and `name:` must match.
- Keep the first screen focused on the procedure, not background.
- Prefer existing stable wrappers under `scripts/`.
- State whether commands are read-only or write-producing.
- For write-producing workflows, list the verification and manager report step.
- Do not include secrets, tokens, profile values, or tenant-specific IDs.

## Current Skill Groups

- Team lifecycle: `hire`, `fire`
- Operations inspection: `tmux`, `server-status`
- Communication/task SOP: `team-communication`, `task-workflow`
- Template only: `_template`

## Compatibility Rule

Existing user commands must remain valid:

- `/hire <role> <description>`
- `/fire <agent>`
- `/tmux [agent] [lines]`
- `python3 scripts/feishu_msg.py ...`
- `python3 scripts/task_tracker.py ...`

When a workflow is converted to a skill, keep the command wrapper stable and
make the skill document the supported path. Do not force users or agents to
learn a new command during the first migration phase.
