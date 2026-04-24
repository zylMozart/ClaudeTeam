# Independent Container Live Smoke

TASK-031 requires a real isolated Feishu test group smoke. Host-only and no-live
evidence are not sufficient for this gate.

## Credential Boundary

Live credentials must stay in `/home/admin/projects/restructure/.env` or
project-local credential directories. Do not paste secrets into Feishu messages,
tmux prompts, git diffs, logs, or issue text. The smoke group link may be
reported, but App secrets, OAuth files, and encrypted key material must not be
printed.

## Minimum Admin Actions If Credentials Are Missing

Provide these out of band. Do not paste secrets into Feishu messages, tmux
prompts, git diffs, logs, or issue text.

1. Create or approve an independent Feishu test App/bot for restructure smoke.
2. Create an independent Feishu test group, add only the test bot and approved
   smoke participants, and provide its `chat_id` plus non-secret `share_link`.
3. Place `FEISHU_APP_ID` and `FEISHU_APP_SECRET` into
   `/home/admin/projects/restructure/.env`, then `chmod 600 .env`.
4. Provide Claude/Codex credentials for the container through project-local
   credential directories or API env vars, then `chmod 700` the directories.
5. Keep `CLAUDETEAM_ENABLE_BITABLE_LEGACY=0` or unset. Live smoke only needs
   `CLAUDETEAM_ENABLE_FEISHU_REMOTE=1`.

## Boundary Checks

```bash
cd /home/admin/projects/restructure
umask 077
chmod 600 .env scripts/runtime_config.json
mkdir -p state workspace/shared/facts workspace/shared/.pending_msgs \
  workspace/shared/live_smoke/raw_tmux .lark-cli-credentials .claude-credentials \
  .codex-credentials .kimi-credentials .gemini-credentials .qwen-credentials
chmod 700 state workspace/shared/facts workspace/shared/.pending_msgs \
  workspace/shared/live_smoke .lark-cli-credentials .claude-credentials \
  .codex-credentials .kimi-credentials .gemini-credentials .qwen-credentials

git check-ignore .env scripts/runtime_config.json workspace agents \
  .lark-cli-credentials .claude-credentials .codex-credentials \
  .kimi-credentials .gemini-credentials .qwen-credentials state
docker compose config --quiet
docker compose -f docker-compose.prod-hardened.yml --profile prod-hardened config --quiet
```

## Dev-Smoke Container Start

Use a distinct compose project name. Do not use the current production/main
group link.

```bash
cd /home/admin/projects/restructure
COMPOSE_PROJECT_NAME=claudeteam-restructure-live \
CLAUDETEAM_ENABLE_FEISHU_REMOTE=1 \
CLAUDETEAM_ENABLE_BITABLE_LEGACY=0 \
docker compose -f docker-compose.yml -f docker-compose.live-smoke.override.yml \
  up -d --build team
```

The entrypoint starts the live router only when
`CLAUDETEAM_ENABLE_FEISHU_REMOTE=1`. The Bitable kanban daemon remains disabled
unless `CLAUDETEAM_ENABLE_BITABLE_LEGACY=1`, which this smoke must not set.

`docker-compose.live-smoke.override.yml` is for the default dev-smoke compose
file only. Do not combine this override with `docker-compose.prod-hardened.yml`.
Router/watchdog PID and cursor files now default to `CLAUDETEAM_STATE_DIR`, so
prod-hardened live smoke uses `/app/state`. After the owner clarified the TASK-032
acceptance criteria, this profile keeps the host boundary strict but gives the
container manager full control inside the container; `/app`, `/app/scripts`,
HOME/cache, state, and workspace are writable.

## Prod-Hardened Live Smoke Start

Use this for TASK-032 final validation:

```bash
cd /home/admin/projects/restructure
COMPOSE_PROJECT_NAME=claudeteam-restructure-live \
CLAUDETEAM_ENABLE_FEISHU_REMOTE=1 \
CLAUDETEAM_ENABLE_BITABLE_LEGACY=0 \
docker compose -f docker-compose.prod-hardened.yml \
  --profile prod-hardened up -d --build --force-recreate team-prod-hardened
```

Validation:

```bash
docker inspect claudeteam-restructure-live-team-prod-hardened-1 \
  --format 'user={{.Config.User}} readonly={{.HostConfig.ReadonlyRootfs}} privileged={{.HostConfig.Privileged}} network={{.HostConfig.NetworkMode}} health={{.State.Health.Status}}'
docker exec claudeteam-restructure-live-team-prod-hardened-1 sh -lc '
  env | grep -E "CLAUDETEAM_ENABLE_(FEISHU_REMOTE|BITABLE_LEGACY)|CLAUDETEAM_STATE_DIR" | sort
  ps -eo pid,args | grep -E "[c]odex|[f]eishu_router.py|[w]atchdog.py"
  ps -eo pid,args | grep -E "[k]anban_sync" || true
  ls -la /app/state
  touch /app/.write_test /app/scripts/.write_test /home/claudeteam/.cache/write_test /app/state/write_test /app/workspace/write_test
  rm -f /app/.write_test /app/scripts/.write_test /home/claudeteam/.cache/write_test /app/state/write_test /app/workspace/write_test
'
docker logs claudeteam-restructure-live-team-prod-hardened-1 2>&1 |
  grep -E 'npm install -g @openai/codex|@openai/codex@latest|EROFS|Read-only file system|OSError|router \+ kanban PID 就位|kanban_sync|secret' || true
```

## Boss Message Simulation

The actual acceptance message must be sent by an approved boss/test-user
identity in the independent Feishu test group. Use a nonce and short fixed text,
for example:

```text
SMOKE-<nonce> container manager: acknowledge and ask coder plus qa_smoke to reply with this nonce.
```

Expected behavior:

- container `manager` receives the group message and replies in the test group
- `manager` dispatches at least one or two employees, for example `coder` and
  `qa_smoke`
- the selected employees respond with the nonce
- no Bitable/kanban daemon is started

Rehearsal-only event injection is not currently automated. Inject a tmux event
manually for local rehearsal before the real boss/test-user sends the Feishu
group message.

## Extended Roll-Call Smoke (P5 expanded scope, added 2026-04-24)

Owner feedback (2026-04-24) tightened the smoke bar. A passing live smoke now
requires all three gates below, not just a single boss→manager exchange.

### Gate A — Full-team roll call

Boss identity (own user or `lark-cli --as user` impersonation via device-flow
user_access_token) posts exactly this into the test group:

```text
所有员工报道
```

Expected behavior:

- router catches the message and routes it to `manager`
- `manager` does **not** self-reply a status summary. `manager` must dispatch an
  individual wake/report task to each worker via
  `python3 scripts/feishu_msg.py send <worker> manager "<task>" 高`
- every worker in `team.json` (lazy-mode ones must wake from the inbox event)
  posts its own message in the group via
  `python3 scripts/feishu_msg.py say <worker> "..."` carrying at minimum its
  agent name and CLI type (e.g. `我是 worker_codex，CLI=codex-cli，已就绪`)
- the group shows one reply per worker plus an optional manager summary
- no Bitable/kanban daemon is started

Pass criterion: count of distinct worker `say` messages in the group within 90s
of the boss prompt equals the count of workers declared in `team.json` minus
`manager` itself.

### Gate B — Tmux window cleanliness for every agent

For each window in the container tmux session (`manager`, every worker, plus
`router`, `kanban`, `watchdog-*`, `supervisor_ticker`), run:

```bash
docker exec <container> tmux capture-pane -pt <session>:<window> -S -50
```

Each window must be free of:

- garbled prompt head/tail, literal `\n`, or command-fragment prefixes
- stray inbox/send/spawn lines left from prior dispatches
- broken escape sequences or UTF-8 artifacts
- any error banner or crash trace still on screen

Lazy-mode worker windows idling with only the banner
`💤 待 wake  (agent=<name>, ...)\n   router 收到业务消息后会唤醒本窗口\nroot@<id>:/app#`
count as clean.

### Gate C — ClaudeTeam slash commands render correctly

Run each custom slash command end-to-end at least once during the smoke and
confirm the rendered reply/card arrives in the test group without truncation or
schema error:

| command    | entry point                                          | expected render                                                |
|------------|------------------------------------------------------|----------------------------------------------------------------|
| `/help`    | typed in group by boss identity                      | help text card listing the six commands                        |
| `/team`    | typed in group                                       | team composition card (one row per agent, status + CLI)        |
| `/usage`   | typed in group                                       | 飞书 card with weekly quota + per-CLI Extra usage snapshot     |
| `/tmux`    | typed in group                                       | tmux window list for the container session                     |
| `/send`    | typed in group with `/send <agent> <text>`           | confirmation of delivery, target agent inbox receives message  |
| `/compact` | typed in group                                       | per-agent context compaction ack                               |

Any command that returns a raw JSON dump, a traceback, a "card schema invalid"
error, or fails silently blocks the smoke. Re-run after the fix.

## Raw Tmux Capture

QA evidence should sample container tmux panes manually:

```bash
docker exec <container> tmux capture-pane -pt <session>:<window> -S -10
```

Sample at least 10 times, 5 seconds apart. A passing cleanliness scan means no
detected prompt pollution such as command remnants, literal `\n`, half inbox
commands, spawn command fragments, or broken escape text.

## Evidence To Report

- compose project name
- container name or `docker compose ps`
- test-group `share_link` only, no secrets
- nonce and message text
- `docker compose logs --tail` excerpt after secret scan
- raw tmux capture output path and `summary.json` verdict
- confirmation that `CLAUDETEAM_ENABLE_BITABLE_LEGACY=0`
- **Gate A** (roll call): message_id list of each worker's individual reply
- **Gate B** (cleanliness): per-window `tmux capture-pane` excerpt with verdict
- **Gate C** (slash commands): screenshot or text of the rendered reply for each
  of `/help /team /usage /tmux /send /compact`
