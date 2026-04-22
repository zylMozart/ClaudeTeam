# Smoke Run Log Template

Copy this template for each dedicated smoke run. Keep reusable procedure changes
in `tests/smoke/`; keep only run-specific facts and evidence in the copied log.

Canonical procedure:

- `tests/smoke/README.md`
- `tests/smoke/checklist.md`
- `tests/smoke/slash_matrix.md`
- `tests/smoke/tmux_boundary.md`

Related historical reference:
`agents/devops/workspace/smoke_container_rebuild_run_20260423.md`.

## Run Identity

- Date:
- Owner:
- Source path:
- Branch:
- Commit:
- New project directory:
- Container:
- Compose project:
- Image:

## Backups

- Backup directory:
- Sanitized config summary:
- Old container:
- Old project directory:
- Old Compose project:
- Old group/chat/table IDs:
- Old tmux capture paths:

## Build And Init

- Documented setup commands used:
- Commands that required undocumented flags or ordering:
- Build/init/start transcript paths:
- Container health result:
- tmux windows:

## Feishu Resources

- Lark profile:
- App ID status:
- Chat ID:
- Group link:
- Message table:
- Status table:
- Kanban table:
- Workspace tables:
  - `manager`:
  - `worker_cc`:
  - `worker_kimi`:
  - `worker_codex`:
  - `worker_gemini`:
- Boss todo table:
- Boss todo link:
- Boss todo dedupe keys:
- Realtime user event result:
- Subscription refresh/re-publish result:

## Report Acceptance

- Manager start message:
- `worker_cc` report evidence:
- `worker_kimi` report evidence:
- `worker_codex` report evidence:
- `worker_gemini` report evidence:
- Manager final summary:
- Status callback results:

## Slash Matrix

Record trigger method as `real_user_event` or `fake_handle_event`.

| Command | Trigger | Group Evidence | Result | Blocker |
| --- | --- | --- | --- | --- |
| `/help` |  |  |  |  |
| `/health` |  |  |  |  |
| `/team` |  |  |  |  |
| `/tmux` |  |  |  |  |
| `/usage` |  |  |  |  |
| `/usage cc` |  |  |  |  |
| `/usage kimi` |  |  |  |  |
| `/usage codex` |  |  |  |  |
| `/usage gemini` |  |  |  |  |
| `/usage all` |  |  |  |  |
| `/compact [agent]` |  |  |  |  |
| `/stop <agent>` |  |  |  |  |
| `/clear <agent>` |  |  |  |  |

## Usage All

| CLI | Usage | Remaining | Refresh/Reset | Blocker |
| --- | --- | --- | --- | --- |
| Claude Code |  |  |  |  |
| Kimi |  |  |  |  |
| Codex |  |  |  |  |
| Gemini |  |  |  |  |

## Worker Send Chain

Current `/send` is raw tmux injection into the target pane. Record it as a tmux
boundary stress path, not as router lazy-wake or inbox delivery.

| Target | Probe | Pane Evidence | Reply Evidence | Result |
| --- | --- | --- | --- | --- |
| `worker_cc` |  |  |  |  |
| `worker_kimi` |  |  |  |  |
| `worker_codex` |  |  |  |  |
| `worker_gemini` |  |  |  |  |

## tmux Boundary

| Pane | Start/End Clean | Stable Idle | Pending Empty | Historical Pollution | Result |
| --- | --- | --- | --- | --- | --- |
| `manager` |  |  |  |  |  |
| `worker_cc` |  |  |  |  |  |
| `worker_kimi` |  |  |  |  |  |
| `worker_codex` |  |  |  |  |  |
| `worker_gemini` |  |  |  |  |  |

## Formatting Convention Notes

Feishu formatting and newline readability issues are recorded here as
manager-prompt or memory convention gaps during smoke-docs work. Do not list
them as renderer/spec/regression implementation tasks unless a separate
code-change request exists.

- Prompt/memory convention gaps:
- Evidence readability issues:
- Follow-up owner:

## Blockers

- Implementation bugs:
- Account, credential, or quota blockers:
- Feishu subscription or app configuration blockers:
- Documentation gaps:
- Prompt/memory convention gaps:

## Final Verdict

- Result: pass/fail
- Evidence bundle:
- Manager report rid:
- Follow-up tasks:
