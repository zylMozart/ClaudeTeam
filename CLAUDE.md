# Working in this repo (Claude Code context)

This is the `rebuild/minimal` branch — a clean-slate ClaudeTeam rewrite.
Read this file before making changes.  See README.md for what the
project is and how a user runs it.

For deployment (host or Docker), follow `docs/DEPLOYMENT.md`.

## Where things live

Business logic lives in `src/claudeteam/` only.  There is **no** parallel
`scripts/` shim layer (that was the old branch's biggest source of
double-residency).  Every command is a Python module under
`src/claudeteam/commands/` registered in `src/claudeteam/cli.py`.

```
src/claudeteam/cli.py           ← top-level dispatch + COMMANDS registry
src/claudeteam/commands/X.py    ← one module per subcommand, ~30 LOC each
src/claudeteam/store/           ← file-backed local state (no DB)
src/claudeteam/runtime/         ← config / paths / tmux / watchdog
src/claudeteam/feishu/          ← lark-cli wrapper + router pipeline
src/claudeteam/agents/          ← CliAdapter base + per-CLI adapters
```

Tests are in `tests/unit/test_*.py` (per-module),
`tests/integration/test_*.py` (end-to-end in-process; auto gate),
and `tests/scenarios/*.md` (operator-run regression playbooks).

## Building rules (READ BEFORE WRITING CODE)

1. **Every new module ships its own unit test in the same commit.**
   Touching `commands/X.py`?  Write `tests/unit/test_commands_X.py`.

2. **Every new public command ships an operator playbook (markdown) in
   `tests/scenarios/` in the same commit.**  Given/When/Then template,
   for human regression checks against a real deployment.

3. **Simplify before pulling from the old tree.**  The old branch
   accumulated 33 K LOC; the rebuild is currently ~8 K (src + tests).
   Don't bring over `CliCapabilities` dataclasses or 11-file decomposed
   `supervision/` trees.  If a function looks too short to need its
   own file, it probably is.

4. **No compatibility wrappers.**  If an old `scripts/feishu_msg.py`
   call site would break under the new layout, that's fine — we are
   rebuilding, not migrating.

5. **Test fixtures live in `tests/helpers.py`.**  Use `isolated_env()`
   and `run_cli()`.  Don't copy-paste a new `_isolated_state()` per
   file (R16 deleted ~150 LOC of that duplication).

## Simplicity gate (read before opening a PR)

Inspired by `forrestchang/andrej-karpathy-skills` CLAUDE.md.  Before
merging a refactor or new module, walk this checklist:

- **Two-use rule.**  Helpers, dataclasses, and base classes only earn
  their own existence at the *third* call site.  Two similar blocks
  inline beats one premature abstraction.
- **Dead code = delete.**  An unused private function isn't
  "documentation" — it's noise that drifts.  If `grep -rn '\b_fn\b'`
  shows only the definition, remove it.
- **Single-file ceiling: ~300 LOC.**  Past that, ask whether the file
  is doing two jobs.  If yes, split.  If no, leave it.
- **Match the canonical command.**  `commands/health.py` is the
  reference shape: `_check_*` helpers + `HealthReport` accumulator +
  `_emit_text` / `_emit_json` + `main(argv)`.  New commands that look
  drastically different need a one-line "why" in their docstring.
- **No compatibility shims for unreleased work.**  If you renamed a
  function nobody outside the repo calls, just rename it everywhere;
  don't leave a wrapper.

## Test gate (must stay green)

```bash
python3 tests/run.py
```

Stdlib-only runner.  Should report `tests: N passed, 0 failed`.
Failing tests block commits.

## How modules cooperate (the message flow)

```
Feishu chat               feishu/subscribe.py    ←─── lark-cli event +subscribe (Popen)
   │                              │
   │ user types in group          │ NDJSON line
   ▼                              ▼
       ┌─────────────────────────────────────────────┐
       │ feishu/router.py                            │  pure decision:
       │   classify_event(event, agents, …)          │  DROP / ROUTE
       └─────────────────────┬───────────────────────┘
                             │ Decision
                             ▼
       ┌─────────────────────────────────────────────┐
       │ feishu/deliver.py                           │  side-effects:
       │   apply(decision, …) → DeliveryReport       │  inbox + tmux inject
       └────────┬───────────────────────────┬────────┘
                │                           │
                ▼                           ▼
   store/local_facts                 runtime/tmux + agents/
       inbox.json                    inject text into pane
       status.json                   using each adapter's submit_keys
       logs.jsonl
```

`commands/router.py` is the daemon entry that wraps `subscribe.process_lines`
around `lark-cli event +subscribe` stdout.  Tests use a list-of-lines
fixture instead of a real subprocess.

## Patterns that show up everywhere

- **Env-driven state**: `runtime/paths.state_dir()` re-reads
  `$CLAUDETEAM_STATE_DIR` on every call.  Never cache it at module load.
- **Injected callables for I/O**: every function that touches subprocess
  / files takes an optional `run=` or `read=` argument so tests pass a
  recorder.  See `runtime/tmux.py`, `feishu/lark.py`, `runtime/watchdog.py`.
- **Pure functions where possible**: `feishu/router.classify_event`,
  `agents/*.spawn_cmd`, `commands/*.main` are all side-effect-free
  given their inputs.
- **One file per `claudeteam` subcommand**: don't grow a 900-line
  multi-command file (which is what `scripts/feishu_msg.py` became on
  the old branch).

## What NOT to do

- Don't put business logic under `scripts/` at the repo root.
  Console-script entry is `pyproject.toml` →
  `claudeteam = "claudeteam.cli:main"`. The only thing allowed under
  `scripts/` is self-contained external utilities (e.g. the bundled
  Playwright bot creator at `scripts/feishu_bot_creator/`) — they have
  their own `package.json` / runtime and never import claudeteam.
- Don't reach into other modules' module-level globals from tests.
  Use the injectable kwargs (`run=`, `popen=`, `tmux_inject=`).
- Don't add docs/ subfolders for every concern.  This file + README.md
  + `tests/scenarios/*.md` is the documentation surface.

## Active work order (rough)

1. (done) Local store + 7 commands
2. (done) CLI adapters + lifecycle + tmux wrappers
3. (done) Feishu lark + chat + router pipeline + watchdog + tasks
4. (done) `claudeteam init` bootstrap
5. (done) Identity rendering (`agents/<name>/identity.md` per pane)
6. (done) Lazy wake (placeholder pane → CLI on first message)
7. (done) Router catchup-on-restart (`feishu/catchup.py` + cursor)
8. (done) `claudeteam health`, `up`, `down`, agent heartbeats
9. (done) Slash command hooks (`claudeteam install-hooks` → .claude/commands/)
10. (done) `claudeteam usage` — ccusage wrapper for claude-code agents
11. (done) Rate-limit detection (adapter `rate_limit_markers`, deliver skips)
12. (done) `claudeteam reset` + 15-helper `util.py` shared stdlib
13. (done) Image / file / audio / sticker Feishu messages → placeholder text
14. (done) Post-compact identity reread (`/compact` schedules background re-init)
15. (done) Slash command router-level dispatch (zero LLM `/help /team /tmux /send /compact /stop /clear /usage /health`)
16. (done) Broadcast routing (`@team` / `@all` / `全体X` → fan out to non-sender agents)
17. (done) Lifecycle helper extraction (`runtime/lifecycle.provision_pane`)
18. (done) Dockerfile + compose (base image: python:3.11-slim + tmux + nodejs; agent CLIs left to derived images)
19. (done) Multi-team isolation UX (`claudeteam switch <team-dir>` emits shell exports)
20. (done) Watchdog orphan-reap (kill PPID=1 lark-cli `+subscribe` left by SIGKILL'd router before respawn)
21. (done) Feishu interactive cards for `/help` `/team` `/health` slash replies, with health-aware header colour
22. (done) Watchdog → Feishu chat alert when a daemon enters cooldown
23. (done) Per-agent durable memory (`store/memory.py`, `facts/<agent>/memory.jsonl`) with auto-injection into identity init prompt on wake
24. (done) Memory CRUD CLI: `claudeteam remember` / `recall` / `forget` — R172.b retired the matching `/recall` and `/forget` slash dispatch (boss-flagged not-in-main); CLI form stays for agent-pane use
25. (done) `gemini-cli` adapter; manager identity v2 ported management discipline rules from main (角色边界 / 集合指令必须 dispatch / 巡视核实 / 沟通格式)
26. (done) Lark perf — bypass `npx`'s package-lookup overhead (`feishu/lark._resolve_cli_prefix`); 73s → 0.6s on macOS host
27. (done) Structured `--help` output grouped by `[bootstrap]` / `[team lifecycle]` / `[durable agent memory]` etc.
28. (done) `claudeteam reidentify --all` for batch re-injection across the team
29. (done) Watchdog cooldown alert promoted to red Feishu card with recovery checklist (was plain text)
30. (done) `claudeteam say <agent> <msg> --card` for card-formatted chat replies; manager → blue / worker_* → green template by convention
31. (done) `qwen-code` adapter (alias `qwen-cli`); adapter coverage 5/5 with old main (claude-code / codex-cli / gemini-cli / kimi-code / qwen-code)
32. (done) `claudeteam peek <agent> [N]` branded fast path for the 5-min 巡视 cadence; install-hooks `/peek` + manager identity v2 migrated off raw `tmux capture-pane`
33. (done) Slash hook coverage parity with R83-R96 commands: `/say --card` / `/remember` / `/recall` / `/peek` all in `claudeteam install-hooks`
34. (done) Round C playbook refresh (post-R86 perf reality, "what's already verified piece-meal" map)
35. (done) Round C real-task end-to-end smoke — confirmed 2026-05-05 in test_a chat: boss `@manager 让 worker_cc 数 feishu/ 下 .py 数量，结果 say 到群，你做汇总` → manager dispatched → worker_cc say-ed result → worker_cc also `claudeteam send manager` (per R173 summary-cue hint) → manager posted final summary "任务已闭环". Loop closes when the boss message contains a summary cue (汇总/汇报/总结/报告/summarize/etc); without one, dispatch + chat-only-say still works for casual messages.
