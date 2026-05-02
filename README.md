# ClaudeTeam (rebuild)

A multi-agent CLI orchestrator: gives a team of LLM coding agents (Claude
Code, OpenAI Codex, Moonshot Kimi) one place to read each other's
inbox, status, logs, and tasks; bridges to a Feishu (Lark) chat group so
a human can talk to the team and the team can talk back.

This branch is a **clean-slate rebuild**.  The previous implementation
(on `fix/stabilize-claudeteam-runtime` / `main`) accumulated ~33 K LOC
across ~200 files; we are rebuilding with the smallest possible
footprint, pulling modules from the old tree only when a concrete
capability requires them.  Currently ~5 700 LOC, 235 tests green.

## Quick start

```bash
# 1. Install (editable from the repo)
pip install -e .

# 2. Tell ClaudeTeam where to keep its state and which team to run
export CLAUDETEAM_STATE_DIR="$PWD/state"
cat > team.json <<'JSON'
{
  "session": "MyTeam",
  "agents": {
    "manager":      {"cli": "claude-code", "model": "opus"},
    "worker_codex": {"cli": "codex-cli",   "model": "gpt-5.5"},
    "worker_kimi":  {"cli": "kimi-code"}
  }
}
JSON

# 3. Bring up the team (one tmux session, one window per agent)
claudeteam start

# 4. Use the local message bus right away — no Feishu needed
claudeteam send worker_codex manager "review the auth module"
claudeteam inbox worker_codex
claudeteam status worker_codex 进行中 "auditing auth"
claudeteam team

# 5. (Optional) wire to Feishu — the boss can chat with the team in a Lark group
cat > runtime_config.json <<'JSON'
{ "chat_id": "oc_xxx", "lark_profile": "" }
JSON
claudeteam router &        # daemon: chat → manager pane
claudeteam say manager "标题党：smoke test #$(date +%s)"
```

## Commands

```
local store / inbox
  claudeteam send <to> <from> <message> [priority]
  claudeteam inbox <agent>
  claudeteam read <local_id>
  claudeteam status <agent> [<state> <task> [blocker]]
  claudeteam log <agent> <kind> <content> [ref]
  claudeteam team
  claudeteam workspace <agent> [--limit N]

team lifecycle
  claudeteam start
  claudeteam hire <agent>
  claudeteam fire <agent>

feishu transport
  claudeteam say <agent> <message> [--reply <message_id>] [--as user|bot] [--no-local]
  claudeteam router

supervision
  claudeteam watchdog

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
├── commands/          one module per subcommand (~30 LOC each)
├── store/
│   ├── local_facts.py inbox / status / log (JSON + JSONL, file-locked)
│   └── tasks.py       coordination cards
├── agents/            CliAdapter + ClaudeCode / Codex / Kimi
├── runtime/
│   ├── config.py      team.json + runtime_config.json
│   ├── paths.py       env-driven $CLAUDETEAM_STATE_DIR layout
│   ├── tmux.py        pane / window / inject wrappers
│   └── watchdog.py    process supervisor (ProcessSpec + supervise sweep)
└── feishu/
    ├── lark.py        npx @larksuite/cli wrapper
    ├── chat.py        send_text / send_card / list_recent
    ├── router.py      pure event → Decision classifier
    ├── deliver.py     Decision → write inbox + inject pane
    └── subscribe.py   NDJSON event loop (drives `claudeteam router`)

tests/
├── unit/              pure-module tests (mocked I/O)
├── smoke/             end-to-end in-process tests + scenarios/*.md
├── helpers.py         isolated_env() + run_cli() shared fixtures
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
tests: 235 passed, 0 failed
```

## What's missing

Documented honestly because some of it is needed for production use:

- **Setup bootstrap**: no `claudeteam init` yet; you write `team.json`
  and `runtime_config.json` by hand
- **Lazy wake**: worker panes always start with their CLI running; the
  old branch supported placeholder panes that woke on first message
- **Slash command interceptors**: no `.claude/hooks/` yet; agents in
  Claude Code invoke `claudeteam X` via Bash directly
- **Bitable / kanban projection**: skipped by design — local facts only
- **Docker deployment**: no Dockerfile / compose files yet
- **Multi-team isolation**: env-var-based; depends on the operator
  setting `CLAUDETEAM_STATE_DIR` and unique tmux session names per team

The rebuild is on `rebuild/minimal`; it does not share history with
`main`.  See `tests/smoke/scenarios/*.md` for natural-language scenarios
covering each feature.
