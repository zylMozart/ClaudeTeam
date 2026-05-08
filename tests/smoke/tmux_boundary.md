# tmux Message Boundary Checks

This check verifies that messages enter each CLI pane cleanly and that the pane
returns to a stable prompt after handling the message. Run it after report
acceptance and after the `/send` matrix.

Reference incident log:
`agents/devops/workspace/smoke_container_rebuild_run_20260423.md`.

This document is about tmux input and output boundaries. Feishu group formatting
or newline presentation problems should be logged as prompt/memory convention
issues during smoke-docs work, not converted into renderer/spec/regression code
changes without a separate implementation request.

## Capture Scope

Capture these panes:

- `<session>:manager`
- `<session>:worker_cc`
- `<session>:worker_kimi`
- `<session>:worker_codex`
- `<session>:worker_gemini`

Set `<container>` to the smoke Compose container name and `<session>` to
`team.json.session`. For the 2026-04-23 reference run these were
`claudeteam-slash-smoke-team-1` and `slash-smoke`; future runs should not assume
those names.

Suggested command:

```bash
container="<container>"
session="<session>"
docker exec "$container" bash -lc '
session="$1"
for w in manager worker_cc worker_kimi worker_codex worker_gemini; do
  echo "===== $w ====="
  tmux capture-pane -pt "$session:$w" -S -160 -J 2>/dev/null | tail -120
done
' _ "$session"
```

Check pending queues:

```bash
container="<container>"
docker exec "$container" bash -lc '
if [ -d workspace/shared/.pending_msgs ]; then
  ls -la workspace/shared/.pending_msgs
  for f in workspace/shared/.pending_msgs/*.json; do
    [ -e "$f" ] || continue
    echo "===$f==="
    cat "$f"
  done
else
  echo pending_dir_absent
fi
'
```

## Delivery Paths To Distinguish

- Router user-message delivery wakes lazy agents and uses CLI-aware idle
  injection before falling back to pending queue files.
- Internal inbox delivery asks the agent to run `feishu_msg.py inbox`.
- Slash `/send` currently sends raw tmux keys directly to the target pane. Treat
  every `/send` probe as a boundary stress test: it can expose shell pollution,
  duplicate input, stale composer content, or missing submit behavior that the
  router lazy-wake path may avoid.

## Universal Pass Criteria

A pane is clean when:

- The latest task begins with the intended first character and ends with the
  intended final punctuation or token.
- There is no active input containing CLI startup commands such as
  `CODEX_AGENT=...`, `KIMI_AGENT=...`, `GEMINI_AGENT=...`, `claude --...`,
  `codex --...`, `kimi --...`, or `gemini --...`.
- There are no visible bare `\n` artifacts in the active input.
- The same message is not injected more than once into the active input.
- No natural-language task was submitted to bash or printed as
  `command not found`.
- No message is visibly truncated at the start or end.
- After command completion, the pane is at a stable idle prompt or a documented
  busy state with a clear running command.
- Pending queue files are absent, empty, or fully explained.

## Universal Fail Criteria

Mark the boundary check failed if any active pane shows:

- `CODEX_AGENT=...` or another CLI spawn command occupying the input box.
- Text from a user task at a shell prompt.
- `command not found` caused by a natural-language message.
- An unsubmitted task stuck in the input box after the worker should have run it.
- Duplicate active copies of the same task.
- A task whose start or end was cut off.
- A non-empty pending queue with no active delivery attempt.

Historical pollution in scrollback should be recorded separately. It does not
fail the current boundary check unless it is still active or affects the current
message.

## CLI-Specific Normal Boundaries

### Claude Code

Normal examples:

- Idle prompt shows a composer line such as `❯` and a status footer.
- Completed shell tasks appear as `Bash(...)` blocks with success or failure
  output.
- The input box is empty after completion.

Abnormal examples:

- A startup command remains in the composer.
- A message appears twice in the composer.
- The pane shows ongoing `Running...` after the expected completion window
  without a clear long-running command.

### Kimi CLI

Normal examples:

- Prompt footer includes `yolo agent (Kimi-...) /app`.
- User task appears once as a Kimi message.
- Completion returns to an empty input area.

Abnormal examples:

- `KIMI_AGENT=worker_kimi kimi --yolo` is treated as a user request inside Kimi
  instead of being only a shell spawn command.
- Natural language appears at a bash prompt.
- The CLI asks whether to retry a failed status update and remains blocked when
  the smoke expected a completed idle state.

### Codex CLI

Normal examples:

- Prompt footer includes `gpt-5.4 default · /app` or equivalent model status.
- Task text appears once after `›`.
- The response completes and the composer is empty.

Abnormal examples:

- `CODEX_AGENT=... codex --dangerously-bypass-approvals-and-sandbox` remains in
  the active input.
- Stale default prompts such as `Explain this codebase` remain as active input
  after the smoke command.
- A background terminal keeps retrying without a bounded status.

### Gemini CLI

Normal examples:

- Footer shows `YOLO`, `/app`, and the selected model.
- Input box returns to `Type your message or @path/to/file`.
- The response is printed once.

Abnormal examples:

- `GEMINI_AGENT=... gemini --approval-mode=yolo` appears in the Gemini input box
  as task text.
- The injected message is visible in the input box and never submitted.
- The same response appears multiple times without a clear reason.

## Current Smoke Run Findings To Record

The 2026-04-23 smoke run found these boundary classes:

- Clean current pane: `worker_cc` after completion returned to an empty Claude
  Code prompt.
- Historical pollution but current prompt usable: `worker_kimi`, `worker_gemini`
  showed previous startup-command/task text in scrollback. Kimi also had a
  previous status retry prompt due to API limits.
- Needs code/process fix: raw `tmux send-keys` into lazy placeholder windows can
  send natural language to bash and produce `command not found`. Use lifecycle
  wake plus CLI-aware idle injection instead.
- Needs follow-up when active: `worker_codex` showed earlier stale prompt text
  and retry activity during capture; only mark pass after it returns to an empty
  Codex prompt.
