# ClaudeTeam (rebuild)

A multi-agent CLI orchestrator: gives a team of LLM coding agents (Claude
Code, OpenAI Codex, Moonshot Kimi) one place to read each other's
inbox, status, logs, and tasks; bridges to a Feishu (Lark) chat group so
a human can talk to the team and the team can talk back.

This branch is a **clean-slate rebuild**.  The previous implementation
(on `fix/stabilize-claudeteam-runtime` / `main`) accumulated ~33 K LOC
across ~200 files; we are rebuilding with the smallest possible
footprint, pulling modules from the old tree only when a concrete
capability requires them.  Currently ~8 500 LOC (src + tests), 463 tests green.

## Prerequisites

- Python 3.10+ (the project pins `requires-python = ">=3.10"`)
- `tmux`, `node` + `npx` (for `lark-cli`)
- At least one of: `claude` (Claude Code CLI), `codex` (OpenAI Codex CLI),
  `kimi` (Moonshot Kimi CLI) on `$PATH` — whichever your `team.json` agents
  declare via the `cli` field. The team.json `cli` identifiers map as:
  `claude-code` → `claude`, `codex-cli` → `codex`, `kimi-code` → `kimi`.

`team.json` per-agent fields — only `cli` is meaningful for spawning;
`role` shows up in the rendered `identity.md`; `model` falls back to the
top-level `default_model` (then `CLAUDETEAM_DEFAULT_MODEL`, then `"opus"`)
and is silently ignored by adapters that don't honor it (Kimi).

## Quick start

```bash
# 0. Shell setup — set ONCE per terminal, before any claudeteam command.
#    For persistence, add to your shell rc (~/.zshrc / ~/.bashrc).
export CLAUDETEAM_STATE_DIR="$PWD/state"   # else state goes to ~/.claudeteam
export LARK_CLI_NO_PROXY=1                 # required if you have HTTPS_PROXY set
export CLAUDETEAM_LARK_SEND_AS=bot         # required for headless / smoke runs;
                                           # without it `say` defaults to user
                                           # OAuth and fails when OAuth expires

# 1. Install (editable from the repo, in a venv)
#    Many hosts ship Python under uv / Homebrew / system manager that PEP 668
#    blocks bare `pip install` against; a venv side-steps that.
python3 -m venv .venv
source .venv/bin/activate
pip install -e .

# 2. Bootstrap config files in the current directory
claudeteam init                  # writes team.json + runtime_config.json
$EDITOR runtime_config.json      # set chat_id + lark_profile when ready

# 3. Install slash commands BEFORE up (claude-code caches them at pane startup)
claudeteam install-hooks         # writes .claude/commands/{inbox,team,...}.md
                                 # — running this AFTER `up` means existing
                                 # panes won't pick them up; install-hooks
                                 # warns when it detects an active session.

# 4. Bring up the whole team in one shot (tmux + agents + router + watchdog)
claudeteam up
claudeteam health                # green/yellow/red snapshot — no surprises

# 5. Inspect the local inbox / status (these are LOCAL ONLY — see "Two transports" below)
claudeteam send worker_codex manager "review the auth module"   # writes inbox.json
claudeteam inbox worker_codex
claudeteam status worker_codex 进行中 "auditing auth"
claudeteam team                  # shows ♥ heartbeat per agent

# 6. Talk in the Feishu chat (the only path that injects into a worker pane)
#    `say` returns the Feishu message_id — capture it for `--reply` or audit.
claudeteam say manager "标题党：smoke test #$(date +%s)"
# → ✅ manager → chat (message_id=om_xxxxxxxx)

# 7. Tear it all down
claudeteam down
```

## Two transports — `send` is *not* `say`

Two ways a message can travel inside a deployment, and they don't reach
the same place:

| command | what it does | reaches the worker's tmux pane? |
|---|---|---|
| `claudeteam send <to> <from> <msg>` | append a row to the local `inbox.json` | **no** — only `claudeteam inbox <to>` reads it back |
| `claudeteam say <agent> <msg>` | post into the Feishu chat as that agent | only if the router daemon is running and routes the message back |
| Feishu group → router → `deliver.apply` | inbound chat → inbox row + tmux pane inject | **yes** — this is the only path that wakes the worker |

If you want a worker pane to actually receive a task, the message has to
travel through Feishu (humans @-mention from the chat → router picks it
up, OR `claudeteam say <peer>` posts a chat message that mentions the
target). Pure `claudeteam send` writes to the inbox file but does not
touch tmux — that's a deliberate split between persistence and delivery.

### Closed-loop end-to-end test

To verify the whole pipeline (Feishu → router → pane → worker reply):

```bash
# 1. From the Feishu group, post a message tagging a worker with a clear
#    instruction. From the host, you can do this AS YOUR USER OAuth:
claudeteam say boss "@worker_cc 收到这条立刻执行 claudeteam say worker_cc \"[reply] ok\"" --as user

# 2. Wait ~90s (lark-cli +subscribe pulls the event, router classifies,
#    deliver.apply injects into the worker_cc tmux pane, worker reads
#    the prompt, runs the embedded say command, lark-cli posts back).

# 3. Verify worker_cc replied:
LARK_CLI_NO_PROXY=1 npx -y @larksuite/cli --profile $LARK_CLI_PROFILE \
    im +chat-messages-list --chat-id $CHAT_ID --page-size 5 --as bot --format json \
    | grep '\[worker_cc\]'
```

Expected: a `[worker_cc] [reply] ok` row newer than the boss message.
If the loop breaks anywhere, `claudeteam health` + `state/router.cursor`
+ `tmux capture-pane -t ClaudeTeam:worker_cc -p` localize the failure.

## Docker

```bash
docker compose build
docker compose up -d
docker compose exec claudeteam claudeteam install-hooks
docker compose exec claudeteam claudeteam up
docker compose exec claudeteam claudeteam health
docker compose exec claudeteam tmux attach -t ClaudeTeam   # see panes
```

`./team-data/` (mounted at `/data`) holds `team.json` +
`runtime_config.json` + state across container restarts.
`~/.lark-cli/` is mounted read-only so the OAuth profile is reused.

The base image deliberately does NOT bake in `claude` / `codex` /
`kimi` CLIs — each has its own auth + licence, and shipping all three
locks the image to one provider stack. Derive from `claudeteam:dev`
and `RUN` the install you actually need (or bind-mount the host
binary into the container's `$PATH`).

See `tests/scenarios/docker_deploy.md` for the full playbook.

## Operator notes

- **venv must be active in the parent shell** before `claudeteam up`.
  Spawned panes inherit `PATH` from the launching shell. If you start
  a *fresh* terminal (or a fresh `claude` instance from outside the
  project) and try `claudeteam …`, you'll get `command not found`
  unless you `source .venv/bin/activate` first.
- **lark-cli is invoked via npx**, not installed globally. For boss-side
  debug commands, use `npx -y @larksuite/cli ...`. The first invocation
  takes ~30 s while npm fetches the package.
- **`tmux list-windows` markers** — names like `worker_codex-` or
  `worker_kimi*` are tmux's last-active / active markers, not part of
  the window name. `tmux display-message -p '#W'` returns the clean
  name from inside a pane.
- **Per-pane `claudeteam` calls stay in the pane's cwd.** Workers' agent
  identities (`identity.md`) tell them not to `cd` anywhere because
  `runtime_config.json` lives next to the spawn cwd. If a worker
  responds with `chat_id not set`, it's almost always a stray `cd` in
  its first attempt.

## Commands

```
bootstrap & ops
  claudeteam init [--session NAME] [--force]    write team.json + runtime_config.json
  claudeteam version                            print installed package version
  claudeteam up                                 start + router + watchdog (idempotent)
  claudeteam down                               graceful inverse of up
  claudeteam health [--json]                    one-shot deployment-state check
                                                (--json for {ok, bad, warn, lines} dict)

local store / inbox
  claudeteam send <to> <from> <message> [priority]
  claudeteam inbox <agent>
  claudeteam read <local_id>
  claudeteam status <agent> [<state> <task> [blocker]]
  claudeteam log <agent> <kind> <content> [ref]
  claudeteam team [--json]                      status + ♥ heartbeat per agent
                                                (--json for machine-readable list[dict])
  claudeteam workspace <agent> [--limit N]

team lifecycle
  claudeteam start                              tmux session + per-agent panes
  claudeteam hire <agent>                       add a new pane (lazy-aware)
  claudeteam fire <agent>
  claudeteam switch [<team-dir>]                print env exports for multi-team UX
                                                (eval the output to switch shells)

feishu transport
  claudeteam say <agent> <message> [--reply <message_id>] [--as user|bot] [--no-local]
  claudeteam router                             daemon: chat events → inbox + pane

supervision
  claudeteam watchdog                           respawns dead daemons

task tracking
  claudeteam task create <assignee> <title> [--by <agent>] [--desc <text>]
  claudeteam task update <id> [--status S] [--assignee A] [--title T] [--desc D]
  claudeteam task list   [--status S] [--assignee A]
  claudeteam task get    <id>
  claudeteam task done   <id>
```

## Layout

```
src/claudeteam/
├── cli.py             single console-scripts entry; dispatch only
├── util.py            shared helpers: now_ms, fmt_time_ms, ago_ms,
│                      pop_flag, pop_bool_flag, read_json, write_json,
│                      atomic_write_text, flock, env_path, env_str,
│                      help_requested, usage_error, error_exit, warn
├── commands/          one module per subcommand (~30-100 LOC each)
├── store/
│   ├── local_facts.py inbox / status / log / heartbeats (JSON + JSONL, file-locked)
│   └── tasks.py       coordination cards
├── agents/
│   ├── base.py        CliAdapter abstract base
│   ├── claude_code.py / codex_cli.py / kimi_code.py — concrete adapters
│   └── identity.py    per-agent identity.md template renderer
├── runtime/
│   ├── config.py      team.json + runtime_config.json
│   ├── paths.py       env-driven $CLAUDETEAM_STATE_DIR layout
│   ├── tmux.py        pane / window / inject wrappers
│   ├── wake.py        lazy-pane wake (capture + spawn + poll-for-ready)
│   ├── pidlock.py     single-instance daemon lock (acquire / release)
│   └── watchdog.py    process supervisor (ProcessSpec, all_known_specs, supervise)
└── feishu/
    ├── lark.py        npx @larksuite/cli wrapper (call())
    ├── chat.py        send_text / send_card / list_recent
    ├── router.py      pure event → Decision classifier
    ├── deliver.py     Decision → write inbox + inject pane (rate-limit aware)
    ├── subscribe.py   NDJSON event loop (drives `claudeteam router`)
    └── catchup.py     replay missed messages on router restart (cursor-based)

tests/
├── unit/              pure-module tests (mocked I/O via attr_patch)
├── integration/       end-to-end in-process tests
├── scenarios/         operator-run regression playbooks (markdown)
├── helpers.py         isolated_env() + run_cli() + env_patch / attr_patch /
│                      tmux_patch + FakeProc / CallRecorder
└── run.py             stdlib-only runner (no pytest dep)
```

## Building rules

1. Every new module ships its own unit test in the same commit.
2. Every new public command ships a smoke scenario (markdown) in the same commit.
3. Modules pulled from the old tree are simplified before they land —
   no over-decomposition (the old `supervision/` 11-file layout is now
   one ~200 LOC `runtime/watchdog.py`).
4. No "compatibility wrappers".  If old call sites break, they break —
   we are rebuilding, not migrating.

## Test gate

```bash
python3 tests/run.py
```

Stdlib-only runner.  No pytest dependency.  Discovers `tests/unit/test_*.py`
and `tests/integration/test_*.py`, runs every `test_*` function, prints a
summary; non-zero exit on any failure.

```
tests: 463 passed, 0 failed
```

## What's missing

Documented honestly because some of it is needed for production use:

- **Multi-team isolation polish**: still env-var-based at the runtime
  level; `claudeteam switch <team-dir>` now emits the three exports
  in one shell-evaluable line, but operators still need unique tmux
  session names per team (set in each team.json's `session` field).
- **Bitable / kanban projection**: skipped by design — local facts only

What's done that earlier revisions of this list flagged as missing:
image / file / audio / sticker Feishu messages route as
placeholder text instead of dropping; `/compact` schedules a 45 s
delayed identity re-injection so agents reload `identity.md` after
self-compacting; slash-command interceptors via
`claudeteam install-hooks`; `claudeteam usage` (ccusage wrapper);
rate-limit detection (adapter `rate_limit_markers()` +
deliver-skips-when-rate-limited); zero-LLM router-level slash
dispatch (`/help /team /tmux /send /compact /stop /clear /usage
/health`); broadcast routing (`@team` / `@all` / `全体X`).

The rebuild is on `rebuild/minimal`; it does not share history with
`main`.  See `tests/scenarios/*.md` for natural-language scenarios
covering each feature (operator-run regression playbooks).
