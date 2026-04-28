# ADR 0001: Local Core Facts And Legacy Bitable Boundary

Date: 2026-04-29
Status: draft, updated for TASK-020 P0
Owner: toolsmith

## Context

ClaudeTeam historically used Feishu Bitable for several surfaces:

- inbox/message table
- agent status table
- per-agent workspace logs
- kanban table
- boss todo table in some deployments

Recent operations showed that Bitable and lark-cli OpenAPI calls can hit rate
limits or partial failures. When Bitable is on the message/status critical path,
rate limits can become lost messages, missing employee actions, false unread
state, empty workspaces, duplicated kanban rows, or false success.

The P0 correction is stronger than "Bitable projection degradation": the default
implementation must remove Bitable and Feishu remote calls from the core fact
path. Local durable stores/logs are the default source of truth. Bitable, if kept
temporarily, is an explicit opt-in legacy adapter for low-frequency display,
audit, or migration export.

## Decision

1. Core facts are local by default: `LocalInboxStore`, `LocalStatusStore`,
   `LocalEventLog`, and `PendingQueue`/local outbox own the default
   send/inbox/read/status/workspace-log path.
2. The default path for `send`, `direct`, `inbox`, `read`, and `status` must not
   call Bitable, `lark-cli`, or `npx @larksuite/cli`.
   In short: the default path must not call Bitable, `lark-cli`, or
   `npx @larksuite/cli`.
3. Bitable-related code should be deleted where feasible. If temporarily kept,
   it must live behind an explicit opt-in legacy adapter or export command and
   default off.
4. Kanban is a derived view/export only. It must not be used as the canonical
   source for tasks, status, completion, message delivery, or manager patrol.
5. Message delivery must distinguish local durable inbox write, employee/tmux
   notification, pending queue fallback, and optional legacy export as separate
   outcomes.
6. Remote adapter failure must be recorded as local event/error evidence, but it
   cannot change a successful local command into failure.

## Tool Boundaries

| Tool | Boundary | Default Behavior |
|---|---|---|
| `scripts/feishu_msg.py send/direct` | Business message write and notification wrapper. | Write local inbox first, then notify or queue. No Bitable/lark-cli on default path. |
| `scripts/feishu_msg.py inbox/read` | Local inbox query and explicit read marker. | Read/write local inbox only. Do not mark read implicitly. Legacy Bitable ids may be accepted only as compatibility metadata. |
| `scripts/feishu_msg.py status` | Agent self-report surface. | Write local status and event log. No remote projection required for success. |
| `scripts/feishu_msg.py log/workspace` | Local workspace/event log surface. | Append/list local event log by default. Remote audit export is explicit legacy behavior only. |
| `scripts/task_tracker.py` | Local task fact store. | No Bitable dependency; keep as canonical task state for now. |
| `scripts/kanban_sync.py` | Compatibility view/export wrapper. | Default task/status facts stay local. Any Bitable kanban sync is opt-in legacy/export. |
| `scripts/msg_queue.py` | Local pending delivery queue. | User messages should not be silently dropped because tmux/employee notification is unavailable. |

## Kanban Rule

Kanban may answer "what does the derived board currently show?" It must not
answer these questions as the source of truth:

- is task `TASK-NNN` complete?
- is an agent idle or blocked?
- did manager receive the completion report?
- was a message delivered to an agent TUI?

Those must come from task tracker, local status/evidence collector, manager
inbox/local event log, and delivery result respectively.

## Failure Classifier

| Failure | Classification | Command Result |
|---|---|---|
| Local inbox append fails | Core failure | Non-zero; message was not durably accepted. |
| Local read marker fails or id missing | Core failure | Non-zero; do not pretend read. |
| Local status write fails | Core failure | Non-zero; do not pretend status. |
| Local event log append fails after core write | Local audit degraded | Core command may still succeed, but print/store warning when possible. |
| tmux/employee notification unavailable | Delivery degraded | Inbox write succeeds; enqueue pending delivery and report queued/degraded. |
| Bitable create/search/update/list fails | Legacy export failure | Default command remains successful; record stale/error only if legacy export was explicitly enabled. |
| `lark-cli`/`npx` unavailable | Legacy/live unavailable | Default no-live command remains successful; live/export command fails visibly. |

## Consequences

Positive:

- Rate limits no longer imply lost core state because default commands do not
  call Bitable.
- Kanban can be rebuilt, disabled, or deleted without breaking task execution.
- No-live tests can block `lark-cli`, `npx`, network, tmux, and Docker while
  still verifying message/status behavior.

Negative:

- Users need a clear explanation that Bitable is no longer the default system.
- `/team` and patrol need an evidence collector to avoid reading one stale source.
- More explicit "degraded" states must be shown in reports.

## Follow-Up Work

1. Add `DeliveryResult` to message delivery paths.
2. Add evidence collector contract for status/task/workspace/inbox/pending/tmux.
3. Gate or delete remaining Bitable calls from default wrappers.
4. Keep any legacy Bitable export no-live tested with mocked adapter failures.
