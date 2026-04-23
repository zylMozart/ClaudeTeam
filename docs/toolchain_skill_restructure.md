# Toolchain And Skill Restructure

Date: 2026-04-23
Owner: toolsmith
Status: initial implementation plan and compatibility contract

## Scope

This document records the first TASK-016 step in the isolated
`/home/admin/projects/restructure` worktree. It does not move runtime code yet.
The purpose is to make the skill direction and command compatibility rules
explicit before larger package restructuring begins.

## Problem Statement

ClaudeTeam currently has several overlapping surfaces:

- `.claude/skills/*` for human/agent workflow SOPs.
- `.claude/hooks/*` for manager local slash interception.
- `scripts/slash_commands.py` for Feishu slash dispatch and some hook reuse.
- `scripts/feishu_msg.py` for inbox, group cards, status, workspace logs, and tmux notification.
- `scripts/feishu_router.py` for event routing, lazy wake, pending queue, cursor, and slash prefilter.
- `scripts/task_tracker.py` and `scripts/memory_manager.py` for local task and memory state.

The uncomfortable part is not the existence of these files. It is that users
cannot tell which surface is the stable command, which is a workflow helper, and
which is internal runtime code.

## Skill Boundary

Skills should be standard operating procedures. They are appropriate for:

- hiring and firing agents
- inbox triage and manager reporting
- task lifecycle discipline
- read-only status collection
- documented migration checklists

Skills should not own runtime behavior:

- Feishu event parsing
- Bitable storage adapters
- tmux injection safety
- lifecycle wake/suspend decisions
- pending queue behavior
- slash command registry internals

Runtime behavior must remain in tested Python/shell modules. During migration,
the existing `scripts/*.py` commands remain the stable public wrappers.

## Compatibility Contract

The following commands are public and must keep working through the first
restructure phase:

```bash
python3 scripts/feishu_msg.py send <to> <from> "<message>" [priority]
python3 scripts/feishu_msg.py direct <to> <from> "<message>"
python3 scripts/feishu_msg.py say <from> "<message>"
python3 scripts/feishu_msg.py inbox <agent>
python3 scripts/feishu_msg.py read <record_id>
python3 scripts/feishu_msg.py status <agent> <status> "<task>" ["<blocker>"]
python3 scripts/feishu_msg.py log <agent> <type> "<content>" ["<ref>"]
python3 scripts/task_tracker.py list
python3 scripts/task_tracker.py get <TASK-NNN>
python3 scripts/task_tracker.py create <assignee> "<title>" ["<description>"] --by <creator>
python3 scripts/task_tracker.py update <TASK-NNN> --status <status>
bash scripts/start-team.sh
bash scripts/lib/agent_lifecycle.sh {spawn|suspend|wake} <agent>
```

Slash and skill commands that users already know must also remain valid:

```text
/hire <role> <description>
/fire <agent>
/tmux [agent] [lines]
/team
/usage
/health
/stop <agent>
/clear <agent>
```

## Wrapper Strategy

Phase 1 keeps `scripts/` as the executable surface:

```text
scripts/feishu_msg.py      -> stable CLI wrapper
scripts/feishu_router.py   -> stable daemon wrapper
scripts/slash_commands.py  -> stable dispatch wrapper
scripts/tmux_utils.py      -> stable tmux injection import path
scripts/cli_adapters/*     -> stable adapter import and shell bridge path
```

As code moves into a package later, wrappers should be thin:

```python
#!/usr/bin/env python3
from claudeteam.<domain>.<module> import main

if __name__ == "__main__":
    main()
```

Compatibility requirements:

- Keep exit codes for existing scripts.
- Keep help text and command argument order.
- Keep import compatibility for modules currently imported as `from tmux_utils import ...`.
- Do not require users to run a new `ct` command during the first migration.
- Add tests before changing wrapper behavior.

## Skill Directory Standard

The `.claude/skills/README.md` file is now the local skill contract. New skills
must include:

- `SKILL.md`
- `name:` matching the directory name
- `description:` with a short trigger and usage
- explicit read-only/write-producing boundary
- verification step
- manager report step when shared state changes

The `_template` directory is intentionally not a runtime skill.

## New SOP Skills

This step adds two high-frequency SOP skills:

- `team-communication`: standardizes inbox/read/send/say/status/log use.
- `task-workflow`: standardizes task_tracker + status/log/report closure.

These skills reduce repeated identity-template instructions without changing
runtime code.

## Testing

The static check is intentionally no-live:

```bash
python3 tests/static_skill_layout_check.py
```

It validates:

- `.claude/skills/README.md` exists.
- every non-template skill has `SKILL.md`.
- frontmatter includes `name` and `description`.
- `name` matches the directory.
- command examples keep using stable `scripts/` wrappers.

## Next Steps

1. Convert hook help and docs to reference the same skill/command contract.
2. Add a command registry contract test before changing `slash_commands.py`.
3. Only after no-live tests exist, begin moving pure modules such as
   `message_renderer.py` or `tmux_command.py` behind compatibility wrappers.
4. Keep router, lifecycle, and Feishu delivery moves for a later phase with live
   smoke coverage.
