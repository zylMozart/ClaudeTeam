# ClaudeTeam (rebuild)

A multi-agent CLI orchestrator: gives a team of LLM coding agents (Claude
Code, OpenAI Codex, Moonshot Kimi) one place to read each other's
inbox, status, logs, and tasks; bridges to a Feishu (Lark) chat group so
a human can talk to the team and the team can talk back.

This branch is a **clean-slate rebuild**.  The previous implementation
(on `fix/stabilize-claudeteam-runtime` / `main`) accumulated ~33 K LOC
across ~200 files; we are rebuilding with the smallest possible
footprint, pulling modules from the old tree only when a concrete
capability requires them.  Currently ~8 100 LOC (src + tests), 381 tests green.

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
#    If you want these persistent, add to your shell rc (~/.zshrc / ~/.bashrc).
export CLAUDETEAM_STATE_DIR="$PWD/state"   # else state goes to ~/.claudeteam
export LARK_CLI_NO_PROXY=1                 # required if you have HTTPS_PROXY set
# Optional: lock the bot identity for `claudeteam say` so you never need --as
export CLAUDETEAM_LARK_SEND_AS=bot

# 1. Install (editable from the repo, in a venv)
#    Many hosts ship Python under uv / Homebrew / system manager that PEP 668
#    blocks bare `pip install` against; a venv side-steps that.
python3 -m venv .venv
source .venv/bin/activate
pip install -e .

# 2. Bootstrap config files in the current directory
claudeteam init                  # writes team.json + runtime_config.json
$EDITOR runtime_config.json      # set chat_id + lark_profile when ready

# 3. Bring up the whole team in one shot (tmux + agents + router + watchdog)
claudeteam up
claudeteam health                # green/yellow/red snapshot — no surprises

# 4. Inspect the local inbox / status (these are LOCAL ONLY — see "Two transports" below)
claudeteam send worker_codex manager "review the auth module"   # writes inbox.json
claudeteam inbox worker_codex
claudeteam status worker_codex 进行中 "auditing auth"
claudeteam team                  # shows ♥ heartbeat per agent

# 5. Talk in the Feishu chat (the only path that injects into a worker pane)
#    `say` returns the Feishu message_id — capture it for `--reply` or audit.
claudeteam say manager "标题党：smoke test #$(date +%s)"
# → ✅ manager → chat (message_id=om_xxxxxxxx)

# 6. Tear it all down
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

## Commands

```
bootstrap & ops
  claudeteam init [--session NAME] [--force]    write team.json + runtime_config.json
  claudeteam up                                 start + router + watchdog (idempotent)
  claudeteam down                               graceful inverse of up
  claudeteam health                             one-shot deployment-state check

local store / inbox
  claudeteam send <to> <from> <message> [priority]
  claudeteam inbox <agent>
  claudeteam read <local_id>
  claudeteam status <agent> [<state> <task> [blocker]]
  claudeteam log <agent> <kind> <content> [ref]
  claudeteam team                               status + ♥ heartbeat per agent
  claudeteam workspace <agent> [--limit N]

team lifecycle
  claudeteam start                              tmux session + per-agent panes
  claudeteam hire <agent>                       add a new pane (lazy-aware)
  claudeteam fire <agent>

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
├── smoke/             end-to-end in-process tests + scenarios/*.md
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
tests: 381 passed, 0 failed
```

## What's missing

Documented honestly because some of it is needed for production use:

- **Image / file Feishu messages**: text-only on the wire today; the
  router drops `msg_type=image` events as `empty`
- **Post-compact reread**: when a Claude Code agent's context gets
  compacted, identity.md isn't re-injected automatically
- **Docker deployment**: no Dockerfile / compose files yet
- **Multi-team isolation polish**: env-var-based today; depends on the
  operator setting `CLAUDETEAM_STATE_DIR` and unique tmux session names
  per team. Works, but no `claudeteam switch <team>` UX
- **Bitable / kanban projection**: skipped by design — local facts only

What's done that the previous "what's missing" listed:
slash-command interceptors (`claudeteam install-hooks`),
`claudeteam usage` (ccusage wrapper), rate-limit detection
(adapter `rate_limit_markers()` + deliver-skips-when-rate-limited).

The rebuild is on `rebuild/minimal`; it does not share history with
`main`.  See `tests/scenarios/*.md` for natural-language scenarios
covering each feature (operator-run regression playbooks).
