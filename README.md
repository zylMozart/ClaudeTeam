# ClaudeTeam (rebuild)

A multi-agent CLI orchestrator: gives a team of LLM coding agents (Claude
Code, OpenAI Codex, Moonshot Kimi) one place to read each other's
inbox, status, logs, and tasks; bridges to a Feishu (Lark) chat group so
a human can talk to the team and the team can talk back.

This branch is a **clean-slate rebuild**.  The previous implementation
(on `fix/stabilize-claudeteam-runtime` / `main`) accumulated ~33 K LOC
across ~200 files; we are rebuilding with the smallest possible
footprint, pulling modules from the old tree only when a concrete
capability requires them.  Currently ~8 200 LOC (src + tests), 375 tests green.

## Quick start

```bash
# 1. Install (editable from the repo)
pip install -e .

# 2. Bootstrap config files in the current directory
claudeteam init                  # writes team.json + runtime_config.json
$EDITOR runtime_config.json      # set chat_id + lark_profile when ready

# 3. Tell ClaudeTeam where to keep state (otherwise: ~/.claudeteam)
export CLAUDETEAM_STATE_DIR="$PWD/state"

# 4. Bring up the whole team in one shot (tmux + agents + router + watchdog)
claudeteam up
claudeteam health                # green/red snapshot — no surprises

# 5. Use the local message bus
claudeteam send worker_codex manager "review the auth module"
claudeteam inbox worker_codex
claudeteam status worker_codex 进行中 "auditing auth"
claudeteam team                  # shows ♥ heartbeat per agent

# 6. Talk in the Feishu chat (router routes inbound, `say` posts outbound)
claudeteam say manager "标题党：smoke test #$(date +%s)"

# 7. Tear it all down
claudeteam down
```

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
├── util.py            shared helpers: ago_ms, fmt_time_ms, now_ms,
│                      pop_flag, read_json, write_json, atomic_write_text,
│                      flock, env_path, help_requested, usage_error,
│                      error_exit, warn
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
├── helpers.py         isolated_env() + run_cli() + attr_patch / tmux_patch
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
and `tests/smoke/test_*.py`, runs every `test_*` function, prints a
summary; non-zero exit on any failure.

```
tests: 375 passed, 0 failed
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
`main`.  See `tests/smoke/scenarios/*.md` for natural-language scenarios
covering each feature.
