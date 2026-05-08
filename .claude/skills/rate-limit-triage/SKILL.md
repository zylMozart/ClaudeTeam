---
name: rate-limit-triage
description: "Diagnose Feishu/Bitable OpenAPI rate limits and decide degraded-mode handling."
---

# Rate Limit Triage

Use this skill when lark-cli, Feishu, Bitable, workspace logs, inbox, or kanban
operations show rate-limit or partial-success symptoms.

## Boundary

- Read-only by default.
- Do not retry write operations in a loop.
- Do not delete kanban or Bitable rows during triage.
- This skill diagnoses and recommends degraded behavior; runtime retry logic
  belongs in tested code.

## Symptoms

- `OpenAPIBatchAddRecords limited`
- `record-batch-create` failure
- workspace query displayed as empty after API error
- kanban duplicate rows or stale rows
- message says sent but target did not receive tmux notification

## Procedure

1. Identify the failed surface: inbox, status, workspace log, kanban, boss todo, or group card.
2. Separate core fact from projection:
   - task fact: `scripts/task_tracker.py`
   - status self-report: `scripts/feishu_msg.py status`
   - kanban: projection only
   - workspace log: audit only
3. Check whether the command reported full success, partial success, or degraded success.
4. If core message delivery is affected, recommend visible manager/devops alerting.
5. If only kanban is affected, recommend skipping sync and preserving previous projection.

## Read-Only Commands

```bash
python3 scripts/task_tracker.py list
python3 scripts/feishu_msg.py inbox <agent>
python3 scripts/feishu_msg.py workspace <agent>
```

## Report

```bash
python3 scripts/feishu_msg.py send manager <agent> "<surface + impact + degraded-mode recommendation>" 高
```
