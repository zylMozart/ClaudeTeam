# Smoke Test Plan

This directory is the canonical entry point for ClaudeTeam smoke testing.
Historical run notes may live under `agents/<agent>/workspace/`, but new test
plans, checklists, and pass/fail rules should be maintained here.

Reference run used to derive this plan:
`agents/devops/workspace/smoke_container_rebuild_run_20260423.md`.

## Scope

Use this directory for smoke-test design, execution order, evidence standards,
and reusable run checklists.

The standard smoke team in this directory is intentionally fixed to manager plus
four CLI workers:

- `manager` using Codex CLI.
- `worker_cc` using Claude Code.
- `worker_kimi` using Kimi CLI.
- `worker_codex` using Codex CLI.
- `worker_gemini` using Gemini CLI.

This is not the complete set of CLI adapters supported by the codebase.
For example, `qwen-code` is supported by `scripts/cli_adapters/`, but it is not
part of the current standard smoke team. Adding Qwen or any new CLI to the
standard smoke requires an explicit matrix extension, usage expectation, and
tmux boundary section.

Do not use a smoke-docs task to implement product behavior changes. In
particular, Feishu card/text formatting and newline style issues should be
handled as manager prompt or memory conventions unless a separate implementation
task explicitly asks for renderer, spec, or regression-test changes.

Run-specific evidence may be written to an agent workspace or an operations log,
but the procedure should link back here and any reusable improvement should be
folded into this directory.

## Goals

The smoke test proves that a newly built ClaudeTeam container can be operated
from a Feishu group without hidden manual steps.

It must cover:

- Clean rebuild from a documented source branch or commit.
- Feishu resources created from documented setup steps.
- Standard smoke team with manager plus four CLI workers:
  - `manager` using Codex CLI.
  - `worker_cc` using Claude Code.
  - `worker_kimi` using Kimi CLI.
  - `worker_codex` using Codex CLI.
  - `worker_gemini` using Gemini CLI.
- Public report acceptance where every worker reports from its own CLI.
- Slash command behavior in the real smoke group.
- `/usage all` quota visibility for every CLI in the standard smoke matrix.
- tmux message boundary cleanliness for every CLI pane.

## Required Order

1. Create or identify the dedicated smoke environment.
2. Back up the previous smoke container, logs, tmux panes, and Feishu config.
3. Remove only the old smoke Compose project.
4. Clone the requested source from scratch.
5. Build and initialize strictly from README/project documentation.
6. Start the container and verify health.
7. Get the new Feishu group link.
8. Run report acceptance.
9. Run slash matrix.
10. Run tmux boundary checks.
11. Record all blockers, retries, and documentation gaps.

## Pass Criteria

A run passes only when all of the following are true:

- The container is healthy and the expected tmux session/windows exist.
- The Feishu group link is available and usable.
- Manager and all four workers are present in tmux.
- Every worker reports from its own CLI; manager does not impersonate workers.
- Manager sends a final group summary after the four worker reports.
- Slash commands in `slash_matrix.md` produce real group responses.
- `/usage all` shows usage, remaining quota, and refresh/reset time for each
  standard smoke CLI, or clearly marks a credential/permission blocker for that
  CLI.
- The `/send` chain reaches all four CLI workers.
- tmux boundary checks in `tmux_boundary.md` show no active message corruption.
- Pending message queues are empty or explicitly explained.

## Fail Criteria

Fail the run if any of these occur:

- Undocumented manual setup is required and not recorded as a gap.
- The wrong container or Compose project is removed.
- The group link cannot be produced.
- A worker report is sent by manager instead of the worker's own CLI.
- Router cannot route a real user text event, and no documented workaround or
  subscription repair is performed.
- `/usage all` omits a CLI section without a clear blocker.
- A tmux pane contains active startup-command residue, duplicate pending input,
  natural language at a shell prompt, visible truncation, or a non-empty pending
  queue after the test.
- A known API rate limit is treated as success without retry/result evidence.

## Evidence

Each run should preserve:

- Container name, project directory, Compose project, image ID, and commit.
- Sanitized Feishu app/profile/chat/table configuration.
- Group invite link.
- Command transcript or log path for build/init/start.
- Group message summaries for slash commands.
- `tmux capture-pane` summaries for manager and every worker.
- Explicit pass/fail status and unresolved blockers.

## Run Log Template

Use `run_log_template.md` when starting a new smoke run. The completed run log
should link this directory as the source of truth and should list only
run-specific facts, evidence, deviations, and blockers.
