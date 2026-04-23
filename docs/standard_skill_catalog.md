# Standard Skill Catalog Draft

Date: 2026-04-23
Owner: toolsmith
Status: draft catalog for TASK-016

## Catalog Rules

Skills are SOPs. They may call stable wrappers and ask agents to inspect files,
but they must not become hidden runtime implementations.

Every skill should declare:

- trigger and usage
- read-only versus write-producing boundary
- stable command wrappers it calls
- expected evidence
- manager report rule
- no-live verification when possible

## Current Runtime Skills

| Skill | Status | Purpose | Runtime Boundary |
|---|---|---|---|
| `hire` | existing | Add a new agent with team.json, directories, Feishu workspace, tmux startup. | Calls `scripts/hire_agent.py` and `scripts/feishu_msg.py`; does not own lifecycle internals. |
| `fire` | existing | Remove/archive an agent. | Calls `scripts/fire_agent.py` and status wrapper. |
| `tmux` | existing | Human fallback for tmux pane capture. | Read-only capture SOP; runtime slash command remains code. |
| `server-status` | existing | Read-only server inspection. | Uses shell commands; not a daemon. |
| `team-communication` | added | Standard inbox/read/send/say/status/log discipline. | Calls `scripts/feishu_msg.py`. |
| `task-workflow` | added | Task tracker + status/log/report closure. | Calls `scripts/task_tracker.py` and `scripts/feishu_msg.py`. |

## Proposed Priority Skills

| Skill | Status | Purpose | First Version |
|---|---|---|---|
| `feishu-doc-publish` | draft added | Publish or update formal Feishu docs from Markdown with a review/report checklist. | SOP only; uses lark-cli docs command when executing live. |
| `smoke-evidence` | draft added | Collect smoke evidence without losing command/output context. | Starts with no-live `slash_smoke_test.py --dry-run`; live cases require manager/devops approval. |
| `rate-limit-triage` | draft added | Diagnose Feishu/Bitable OpenAPI rate limits and decide degradation behavior. | Read logs, inbox/workspace symptoms, and wrapper outputs; no automatic retries. |
| `runtime-doctor` | draft added | Check runtime/profile/credential/config boundaries without exposing secrets. | Uses local files and existing wrapper help; no token printing. |
| `tmux-boundary-diagnose` | draft added | Diagnose tmux injection boundaries, unsafe residual input, lazy wake, and shell pollution. | Starts read-only; live injection tests require explicit approval. |

## Not Yet Skills

The following stay as code/tests, not skills:

- Feishu router event normalization.
- Pending queue delivery algorithm.
- `InjectionResult` implementation.
- Bitable repository abstractions.
- Slash command registry.
- Credential loading/redaction.

## Promotion Criteria

A draft skill becomes supported when:

1. `python3 tests/static_skill_layout_check.py` passes.
2. Its workflow has at least one no-live verification step.
3. It points to stable public wrappers instead of internal functions.
4. It has a manager report step for shared-state changes.
5. Security confirms no secrets or tenant IDs are embedded.
