---
name: smoke-evidence
description: "Collect smoke-test evidence with command, scope, output summary, and no-live/live boundary."
---

# Smoke Evidence

Use this skill when manager asks for smoke evidence or a regression proof after
toolchain, command, router, tmux, or deployment changes.

## Boundary

- Default is no-live and read-only.
- Live Feishu/tmux/router injection requires explicit manager/devops approval.
- This skill collects evidence; it does not change runtime code.

## Procedure

1. State the scope: no-live, local mocked, container smoke, or live Feishu.
2. Prefer no-live commands first.
3. Capture the exact command, working directory, result, and short output summary.
4. If a live command is needed, explain why no-live is insufficient.
5. Report failures with the first failing command and the likely owner.

## No-Live Commands

```bash
python3 tests/static_skill_layout_check.py
python3 tests/static_public_contract_check.py
python3 -c "from slash_commands import dispatch; matched, r = dispatch('/help'); print(matched, r)"
```

## Evidence Format

```text
Scope: no-live
Command: <exact command>
Workdir: <path>
Result: passed|failed
Key output: <short summary>
Files changed: <paths or none>
Residual risk: <what this did not prove>
```

## Report

```bash
python3 scripts/feishu_msg.py send manager <agent> "<evidence summary>" 高
```
