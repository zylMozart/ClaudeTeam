# ClaudeTeam test harness

This directory contains the no-live pytest harness and smoke-test procedures.

## Marker taxonomy

- `unit`: fast implementation tests with pure logic or monkeypatched boundaries.
- `contract`: compatibility and safety-contract tests.
- `regression`: wrappers around existing regression scripts.
- `integration`: multi-module tests that still avoid real Feishu, tmux, docker, and network boundaries.
- `live`: any test allowed to touch a real external system.
- `live_feishu`: tests that may call real Feishu or lark-cli.
- `tmux`: tests that may call real tmux or inspect live panes.
- `docker`: tests that may call real Docker or Docker Compose.
- `smoke`: live end-to-end smoke tests.
- `manual`: manually orchestrated tests excluded from default runs.
- `allow_subprocess`: local subprocess opt-in for a narrow allowlist. It still blocks tmux, docker, lark-cli, `@larksuite/cli`, `scripts/feishu_msg.py`, and `scripts/feishu_router.py`.

Default pytest runs are no-live:

```bash
python3 -m pytest
```

The default marker expression excludes `live`, `smoke`, `live_feishu`, `tmux`,
`docker`, and `manual`. Unit, contract, regression, and no-live integration
tests must monkeypatch Feishu/tmux/docker/network/subprocess boundaries.

## Live confirmation

Selecting a live-like marker is not enough. Live-like tests require a second
confirmation:

```bash
CLAUDETEAM_LIVE_TESTS=1 python3 -m pytest -m live_feishu
CLAUDETEAM_LIVE_TESTS=1 python3 -m pytest -m tmux
CLAUDETEAM_LIVE_TESTS=1 python3 -m pytest -m docker
CLAUDETEAM_LIVE_TESTS=1 python3 -m pytest -m smoke
```

Without `CLAUDETEAM_LIVE_TESTS=1`, collection skips live-like tests and the
autouse fixture fails closed if one reaches execution.

## When pytest is unavailable

Some hosts do not have pytest installed. On CI or a prepared developer machine,
install the test extra and run the normal entrypoint:

```bash
python3 -m pip install -e '.[test]'
python3 -m pytest
```

On constrained hosts, run these alternative checks before requesting review:

```bash
python3 -m compileall -q tests
python3 tests/static_safety_check.py
python3 -c "import tomllib; tomllib.load(open('pyproject.toml','rb')); print('pyproject ok')"
python3 scripts/regression_boss_todo.py
python3 scripts/regression_tmux_inject.py
python3 scripts/regression_message_sanitizer.py
python3 scripts/regression_message_rendering.py
```

These checks do not replace CI pytest execution, but they validate syntax, marker
configuration, safety guard policy, and the current no-live regression wrappers.
