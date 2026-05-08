# ClaudeTeam Tests

`python3 tests/run_no_live.py` is the default local test entry point.

The default suite is deliberately no-live: it must not call real Feishu, tmux,
Docker, network APIs, or credential-backed tools. The runner installs
`tests/no_live_guard.py` before loading tests and includes the current offline
regression scripts through one stable entry.

Current layers:

- `tests/static_*`: local static checks.
- `scripts/regression_*`: legacy compatibility regression scripts that are
  still callable directly.
- `tests/run_no_live.py`: unified no-live runner for default verification.

Live smoke tests remain separate from this entry and should use explicit smoke
documentation, credentials, and operator approval.
