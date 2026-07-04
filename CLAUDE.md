# Working in this repo (Claude Code context)

Read this file before making changes.  See README.md for what the
project is and how a user runs it.

For deployment (host or Docker), follow `docs/DEPLOYMENT.md`.

## Where things live

Business logic lives in `src/claudeteam/` only.  There is **no** parallel
`scripts/` shim layer.  Every command is a Python module under
`src/claudeteam/commands/` registered in `src/claudeteam/cli.py`.

```
src/claudeteam/cli.py           ← top-level dispatch + COMMANDS registry
src/claudeteam/commands/X.py    ← one module per subcommand, ~30 LOC each
src/claudeteam/store/           ← file-backed local state (no DB)
                                   acp_queue.py = ACP delivery state machine
src/claudeteam/runtime/         ← config / paths / tmux / watchdog
                                   acp.py = JSON-RPC stdio client,
                                   acp_host.py = per-agent ACP workers
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

3. **Keep it small.**  Prefer the smallest thing that works — avoid
   speculative `CliCapabilities`-style dataclasses or deeply decomposed
   multi-file trees.  If a function looks too short to need its own
   file, it probably is.

4. **No compatibility wrappers.**  Don't keep a shim around just because
   an old call site might break — update the call site instead.

5. **Test fixtures live in `tests/helpers.py`.**  Use `isolated_env()`
   and `run_cli()`.  Don't copy-paste a new `_isolated_state()` per
   test file.

## Simplicity gate (read before opening a PR)

Inspired by `forrestchang/andrej-karpathy-skills` CLAUDE.md.  Before
merging a refactor or new module, walk this checklist:

- **Two-use rule.**  Helpers, dataclasses, and base classes only earn
  their own existence at the *third* call site.  Two similar blocks
  inline beats one premature abstraction.
- **Dead code = delete.**  An unused private function isn't
  "documentation" — it's noise that drifts.  If `grep -rn '\b_fn\b'`
  shows only the definition, remove it.
- **Match the canonical command.**  `commands/health.py` is the
  reference shape: `_check_*` helpers + `HealthReport` accumulator +
  `_emit_text` / `_emit_json` + `main(argv)`.  New commands that look
  drastically different need a one-line "why" in their docstring.
- **No compatibility shims for unreleased work.**  If you renamed a
  function nobody outside the repo calls, just rename it everywhere;
  don't leave a wrapper.

## Test gate (must stay green)

```bash
python3 tests/run.py        # needs Python 3.10+
```

Stdlib-only runner (no pytest required).  Should report
`tests: N passed, 0 failed`.  Failing tests block commits.  If the
`python3` on your machine is older than 3.10, invoke an explicit
interpreter, e.g. `python3.12 tests/run.py`.

That gate (unit + in-process end-to-end) verifies a *code change* — no deploy
needed; it's what an agent runs to check its own work.  To verify the
*deployed product* end-to-end like a real user (send Feishu messages → watch
agents respond), drive `tests/scenarios/host_smoke.md`: an agent runs the
`lark-cli --as user` sends + verifies responses itself; only three one-time
steps (Feishu app, user OAuth, CLI login) need a human.  Install first via
`docs/DEPLOYMENT.md`.  The other `tests/scenarios/*.md` are the same shape.

## How modules cooperate (the message flow)

```
Feishu chat               feishu/subscribe.py    ←─── node scripts/feishu_channel/sidecar.js run (Popen)
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
       │   apply(decision, …) → DeliveryReport       │  inbox + delivery
       └────────┬──────────────┬────────────┬────────┘
                │              │            │
                ▼              ▼            ▼
   store/local_facts   store/acp_queue   runtime/tmux + agents/
       inbox.json      queue.json row    inject text into pane
       status.json     (runner=acp,      (runner=tmux, adapter
       logs.jsonl       durable + ACK)    submit_keys)
                               │
                               ▼
                    runtime/acp_host.AcpHost   (inside `claudeteam router`)
                      one worker per ACP agent: claim row →
                      ACP session/prompt over stdio (runtime/acp) →
                      settle(done|failed, stopReason)  ← the ACK
                      transcript.log ← streamed output (viewer pane tails it)
```

Which transport an agent uses: `config.agent_runner(agent)` — explicit
`runner = "acp" | "tmux"` in claudeteam.toml, else "acp" when the CLI has
an ACP adapter (claude-code, codex-cli), else "tmux". ACP agents' CLIs run
as router subprocesses; their tmux window is a read-only `tail -F` viewer.

`commands/router.py` is the daemon entry that wraps `subscribe.process_lines`
around `node scripts/feishu_channel/sidecar.js run` stdout (official
`@larksuite/channel` WebSocket → NDJSON; lark-cli is now egress-only).
Tests use a list-of-lines fixture instead of a real subprocess.

## Patterns that show up everywhere

- **Env-driven state**: `runtime/paths.state_dir()` re-reads
  `$CLAUDETEAM_STATE_DIR` on every call.  Never cache it at module load.
- **Injected callables for I/O**: every function that touches subprocess
  / files takes an optional `run=` or `read=` argument so tests pass a
  recorder.  See `runtime/tmux.py`, `feishu/lark.py`, `runtime/watchdog.py`.
- **Pure functions where possible**: `feishu/router.classify_event`,
  `agents/*.spawn_cmd`, `commands/*.main` are all side-effect-free
  given their inputs.
- **One file per `claudeteam` subcommand**: don't grow a single
  900-line multi-command file.
- **Adapters are provider-agnostic — NEVER hardcode an endpoint, key,
  provider, or model.**  This is a generic open-source project; the
  operator chooses the model backend.  So in `agents/*.py`:
  - **Credential** flows through `runtime/agent_auth` (priority
    **token > login > api_key**; higher overrides lower).  Each adapter
    declares `auth_slots()` — the OpenAI-compatible workers return
    `base.OPENAI_COMPAT_AUTH` (the `api_key` tier reading
    `OPENAI_API_KEY`); claude/codex/kimi have their own.  The resolved
    key is sourced from a private file at spawn — never typed into the
    pane (see `lifecycle.build_spawn_command`).
  - **Endpoint** (`base_url`) comes from `$OPENAI_BASE_URL`, **model**
    from the agent's `team.json` entry.  Don't invent a default model;
    use the passed value.
  - **Provider label**: where a CLI needs one that selects an
    OpenAI-compatible (chat/completions) client, make it env-overridable
    (e.g. `CLAUDETEAM_TRAE_PROVIDER`) with a documented default — don't
    bake in a vendor name.
  - DeepSeek / OpenAI / a local server are just *examples* set via the
    deployment's env (`docker -e`) + `tests/scenarios/*.md`, never source.

## Maintenance recipes (follow these literally)

These are the three changes this repo sees most. Each is a checklist —
work through it top to bottom and you can't go far wrong, no deep
context needed.

**Add a chat slash command** (`/foo` in Feishu):
1. Write `_handle_foo(args, ctx) -> str | dict` in `feishu/slash.py`
   (str = text reply, dict = card).  Runner-aware? Branch on
   `config.agent_runner(agent)` — see `_handle_stop` for the pattern.
2. Register it in `_HANDLERS` and bump the count comment below it.
3. Add one line to BOTH help texts (the English block at the top of
   slash.py and the Chinese `/help` body).
4. Tests in `tests/unit/test_feishu_slash.py`; scenario step if
   operator-visible.

**Add a `claudeteam <cmd>` subcommand:**
1. New file `commands/<cmd>.py` with `main(argv) -> int`, a `USAGE`
   string, `maybe_print_help` first line.  Copy the shape of
   `commands/health.py` (the canonical command).
2. Register in `cli.py` COMMANDS.
3. Same-commit: `tests/unit/test_commands_<cmd>.py` +
   `tests/scenarios/<cmd>.md` (Given/When/Then).
4. Business logic goes in `runtime/` or `store/`, not in the command
   file — commands are thin argv adapters (~30 LOC).

**Add a new agent CLI adapter:**
1. New file `agents/<name>.py` subclassing `CliAdapter`; implement
   `spawn_cmd` / `ready_markers` / `process_name`; override
   `auth_slots()` if the CLI has credentials, `acp_argv()`/`acp_env()`
   if it speaks ACP (then it gets the acp runner by default).
   NEVER hardcode an endpoint / API key / vendor model name (see the
   provider-agnostic rules below).
2. Register in `agents/__init__.py` `_REGISTRY` (+ alias if the package
   name differs).
3. Add a row to the install table in `docs/DEPLOYMENT.md` AND
   `docs/DEPLOYMENT_zh.md`.
4. `tests/unit/test_agents_<name>.py`.

**Debug a live deployment** — everything is a file under
`$CLAUDETEAM_STATE_DIR` (default `./state`):
- `facts/inbox.json` — every routed message (canonical record)
- `facts/status.json` / `heartbeats.json` — agent self-reports + liveness
- `facts/logs.jsonl` — append-only audit (incl. `acp_turn` ACKs)
- `acp/<agent>/queue.json` — ACP delivery states (pending→prompting→done)
- `acp/<agent>/transcript.log` — the agent's streamed output
- `router.log` / `watchdog.log` — daemon output; `router.seen` — dedup
- `claudeteam health` first, always; `claudeteam peek <agent>` for
  "what is it doing right now".

## What NOT to do

- Don't put business logic under `scripts/` at the repo root.
  Console-script entry is `pyproject.toml` →
  `claudeteam = "claudeteam.cli:main"`. The only thing allowed under
  `scripts/` is self-contained external utilities (e.g. the bundled
  `@larksuite/channel` sidecar at `scripts/feishu_channel/`, used for
  both `feishu connect` registration and the WebSocket event ingress) —
  they have their own `package.json` / runtime and never import claudeteam.
- Don't reach into other modules' module-level globals from tests.
  Use the injectable kwargs (`run=`, `popen=`, `tmux_inject=`).
- Don't add docs/ subfolders for every concern.  The documentation
  surface is: this file + the per-package `CLAUDE.md` under
  `src/claudeteam/*/` (folder-local invariants, auto-loaded when working
  there) + README.md + `tests/scenarios/*.md` + `docs/DEPLOYMENT*.md`.
  `docs/advisor/` holds strategy/roadmap notes.  Anything else needs a
  strong reason.
