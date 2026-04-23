# No-Bitable Core No-Live Smoke

This is the TASK-021 acceptance smoke after the manager correction: the default
core path is local and must not call Bitable, `lark-cli`, or `npx
@larksuite/cli`.

## Scope

The default no-live suite proves these local surfaces work without live Feishu
credentials:

- `send -> inbox -> read -> status -> log -> workspace`
- direct message local inbox write and manager copy
- `say` fails closed unless live Feishu is explicitly enabled
- task facts and the local board source are file-backed

Bitable/Feishu code, where still present, is legacy/export behavior only and
must be behind explicit opt-in environment flags. Default tests run with those
flags absent.

The smoke uses the real default command functions. It does not monkeypatch
Feishu/Bitable helpers; `tests/no_live_guard.py` blocks live tools so leaked
`npx`, `@larksuite/cli`, `lark-cli`, tmux, Docker, or network calls fail the
test.

## Commands

Host:

```bash
cd /home/admin/projects/restructure
python3 tests/no_bitable_core_smoke.py
python3 tests/run_no_live.py
```

The default gate is `python3 tests/run_no_live.py`.

Wrapper:

```bash
cd /home/admin/projects/restructure
python3 scripts/run_no_live_tests.py
```

Container with an isolated Docker config:

```bash
cd /home/admin/projects/restructure
tmp_docker_config=$(mktemp -d /tmp/restructure-docker-config.XXXXXX)
DOCKER_CONFIG=$tmp_docker_config \
COMPOSE_PROJECT_NAME=claudeteam-restructure \
COMPOSE_BAKE=false \
docker compose run --rm --no-deps --entrypoint python3 team tests/run_no_live.py
rc=$?
rm -rf "$tmp_docker_config"
exit $rc
```

This command does not require `docker compose run team init` and does not need
Feishu credentials because it overrides the entrypoint and runs only offline
tests.

## Expected Evidence

Successful host/container output includes:

```text
OK: no_bitable_core_smoke passed
no-live tests: 6/6 passed
```

Preserve the exact command, compose project name, image tag or build reference,
and final pass line when reporting evidence.

## Failure Classifier

| Probe Result | Meaning | Expected Default Behavior |
|---|---|---|
| any Bitable helper called | default path leaked into legacy adapter | fail the no-live smoke |
| `lark-cli` or `npx` invoked | default path leaked into live remote tooling | fail the no-live smoke |
| local inbox write fails | core message failure | command must not claim delivered |
| local read id missing | core read failure | `read` exits non-zero |
| local status/log write fails | core fact failure | command must not claim saved |
| live `say` requested without opt-in | remote disabled by default | fail closed before any lark call |
