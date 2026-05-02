# ClaudeTeam (rebuild)

This branch is a **clean-slate rebuild** of the ClaudeTeam framework.

The old code (on `fix/stabilize-claudeteam-runtime` and `main`) accumulated
~33K LOC across ~200 files with substantial over-decomposition,
test-pin-induced rigidity, and `scripts/` ↔ `src/` double-residency.  Rather
than refactor it round-by-round we are rebuilding with the smallest
possible footprint and pulling in modules from the old tree only when a
concrete capability requires them.

## Direction

```
src/claudeteam/        ← all business logic; one package, no scripts/ shim layer
├── __init__.py
├── cli.py             ← single `claudeteam` console_scripts entry, dispatch only
├── store/             ← local durable inbox + status + log (file-backed)
├── transport/         ← (added when needed) Feishu push/pull, tmux UI delivery
├── agents/            ← (added when needed) CliAdapter capabilities + lifecycle
└── runtime/           ← (added when needed) config, paths, watchdog

tests/
├── unit/              ← module-scoped, mocked I/O
└── smoke/scenarios/   ← natural-language Given/When/Then for live runs
```

No `scripts/feishu_msg.py` style wrappers.  Every entry point is
`claudeteam <subcommand>` exposed via `pyproject.toml`'s console_scripts.

## Building rules

1. Every new module needs a unit test in the same commit.
2. Every public command needs a smoke scenario (markdown) in the same commit.
3. Modules pulled from the old tree must be simplified before they land —
   no over-decomposed helper files (the old `supervision/` 11-file layout
   does not survive).
4. No "compatibility wrappers".  If old call sites break, they break — we
   are rebuilding, not migrating.

## Status

Foundation only.  Run `python3 -m pytest` from the repo root after each
round; expected count grows as modules land.
