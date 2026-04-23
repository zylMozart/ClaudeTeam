---
name: runtime-doctor
description: "Inspect runtime_config, lark profile, team.json, and credential boundaries without printing secrets."
---

# Runtime Doctor

Use this skill when setup, router, lark-cli profile, model selection, or
credential isolation appears broken.

## Boundary

- Read-only.
- Do not print secrets or token values.
- Do not modify `.env`, credential directories, runtime_config, or lark profiles.

## Procedure

1. Confirm `team.json` exists and list only non-secret fields: session, agent names, CLI names.
2. Confirm `scripts/runtime_config.json` exists and list keys only, redacting values.
3. Check public wrapper availability.
4. Confirm profile selection logic via `scripts/config.py` help/commands where possible.
5. Report missing files, ambiguous profile state, or credential boundary risks.

## Read-Only Commands

```bash
python3 scripts/config.py resolve-model manager
python3 scripts/config.py resolve-thinking manager
python3 scripts/task_tracker.py list
```

For JSON inspection, print keys and agent names only. Do not print token values.

## Report

```bash
python3 scripts/feishu_msg.py send manager <agent> "<doctor findings + redaction note>" 高
```
