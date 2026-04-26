# Smoke Checklist

Use this checklist for every dedicated smoke rebuild. Do not rely on informal
instructions in agent workspaces; copy run-specific findings into the run log and
keep this checklist current.

## 1. Source And Backup

- [ ] Record source path, branch, and commit.
- [ ] Record whether remote push is available; if blocked, document why local
      source is still acceptable.
- [ ] Identify old smoke container and Compose project.
- [ ] Back up old `team.json`, `.env`, `scripts/runtime_config.json`,
      `docker-compose.yml`, docker inspect output, tmux windows, and pane tails.
- [ ] Store secret-bearing backups in a local restricted directory.
- [ ] Create a sanitized config summary for the run log.
- [ ] Start a run log from `tests/smoke/run_log_template.md`.

## 2. Clean Rebuild

- [ ] Stop/remove only the old smoke Compose project.
- [ ] Verify other team containers remain running.
- [ ] Clone the requested branch/commit into a clean directory.
- [ ] Create the standard smoke `team.json` with:
  - [ ] `manager`
  - [ ] `worker_cc`
  - [ ] `worker_kimi`
  - [ ] `worker_codex`
  - [ ] `worker_gemini`
- [ ] Confirm this run is scoped to the standard smoke CLI matrix only:
      Claude Code, Kimi, Codex, and Gemini. Code-supported CLIs outside this
      matrix, such as `qwen-code`, require a separate smoke extension.
- [ ] Create `.env` from documented fields.
- [ ] Create the documented runtime config placeholder if required.
- [ ] Run documented build command.
- [ ] Record build gaps such as buildx/Bake fallback requirements.
- [ ] Run documented init command.
- [ ] Run documented start command.
- [ ] Verify container health and tmux windows.

## 3. Feishu Resources

- [ ] Record lark profile.
- [ ] Record chat ID.
- [ ] Record group invite link.
- [ ] Record message/status/kanban table IDs.
- [ ] Confirm bot can see the new group with chat search or equivalent.
- [ ] Verify whether realtime `im.message.receive_v1` receives a real user text
      event. Bot/app cards do not prove realtime user-event delivery.
- [ ] If real user text does not reach router, refresh/re-publish
      `im.message.receive_v1` for the App and retest.

## 4. Report Acceptance

- [ ] Manager publicly announces that worker reporting is starting.
- [ ] Manager wakes or confirms each worker CLI is running.
- [ ] Manager injects tasks through lifecycle wake plus CLI-aware idle injection,
      not raw shell `tmux send-keys` into lazy placeholders.
- [ ] `worker_cc` sends its own group report and manager callback.
- [ ] `worker_kimi` sends its own group report and manager callback.
- [ ] `worker_codex` sends its own group report and manager callback.
- [ ] `worker_gemini` sends its own group report and manager callback.
- [ ] Every worker status is updated or a rate-limit blocker is recorded.
- [ ] Manager sends final group summary after all four worker reports.
- [ ] Bitable rate limits are retried or recorded with exact error codes.

## 5. Slash Matrix

- [ ] Execute non-agent slash commands in `slash_matrix.md`.
- [ ] Prefer a real Feishu user text event for end-to-end routing evidence.
      If fake `feishu_router.handle_event` events are used, mark them as router
      handler evidence only.
- [ ] Capture group card/text summaries for:
  - [ ] `/help`
  - [ ] `/health`
  - [ ] `/team`
  - [ ] `/tmux`
  - [ ] `/usage`
  - [ ] `/usage cc`
  - [ ] `/usage kimi`
  - [ ] `/usage codex`
  - [ ] `/usage gemini`
  - [ ] `/usage all`
- [ ] Verify `/usage all` has each CLI section:
  - [ ] Claude Code usage, remaining quota, reset time.
  - [ ] Kimi usage, remaining quota, reset time.
  - [ ] Codex usage or explicit permission/credential blocker.
  - [ ] Gemini usage, remaining quota, reset time.
- [ ] Execute `/send` or an equivalent command path to every worker.
- [ ] Record that current `/send` implementation uses raw `tmux send-keys` to
      the target pane. Treat it as a tmux boundary risk probe, not as equivalent
      to router lazy-wake/inbox delivery.
- [ ] Confirm each worker pane receives and handles its message.
- [ ] Exercise `/compact`, `/stop`, and `/clear` only with explicit smoke-safe
      targets and evidence. These commands mutate live CLI state and should be
      treated as operational risk checks, not routine read-only probes.

## 5A. Feishu Formatting Convention

This section records prompt and memory expectations only. Do not treat failures
here as approval to change Feishu renderer/spec/regression code during a smoke
docs task.

- [ ] Manager prompt/memory tells agents to keep group replies concise and
      avoid unsupported markdown constructs.
- [ ] Group replies avoid relying on fragile blank-line formatting for meaning.
- [ ] Any newline/card rendering issue is recorded as a prompt/memory convention
      gap unless there is a separate implementation task.
- [ ] If a formatting issue affects smoke evidence readability, preserve the raw
      message text or screenshot and continue functional validation.

## 6. tmux Boundary Check

Run `tmux_boundary.md` after report acceptance and after `/send` matrix.

- [ ] Capture manager pane.
- [ ] Capture `worker_cc` pane.
- [ ] Capture `worker_kimi` pane.
- [ ] Capture `worker_codex` pane.
- [ ] Capture `worker_gemini` pane.
- [ ] Check message start and end boundaries.
- [ ] Confirm no `CODEX_AGENT=...`, `KIMI_AGENT=...`, `GEMINI_AGENT=...`, or
      CLI spawn command is left as active user input.
- [ ] Confirm no bare `\n` artifacts are visible in the active input.
- [ ] Confirm no natural-language task was submitted to bash.
- [ ] Confirm no duplicate active injection of the same task.
- [ ] Confirm no truncation of the active task.
- [ ] Confirm the pane is at a stable idle prompt or a documented busy state.
- [ ] Confirm pending queue files are absent or empty.
- [ ] For panes touched by `/send`, explicitly check whether raw tmux injection
      left residual input, submitted text to bash, duplicated content, or
      bypassed lazy-wake/inbox protections.

## 7. Final Result

- [ ] Mark the run as pass/fail.
- [ ] List blockers that need implementation changes.
- [ ] List blockers that need account/credential/user action.
- [ ] List prompt/memory convention gaps separately from implementation bugs.
- [ ] Link the run log.
- [ ] Send manager a concise status with evidence paths.
