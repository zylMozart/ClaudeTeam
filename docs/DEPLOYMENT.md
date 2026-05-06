# Deployment Guide

End-to-end setup for ClaudeTeam. Covers host-native, Docker, config,
multi-team isolation, and common failures.

## Bringing up a team end-to-end

Whether you're a human reading this or an AI agent driving the
deployment, the flow is the same:

1. **Feishu app** — ask the user whether they already have an
   enterprise custom app + bot. If **yes**, just collect `App ID`,
   `App Secret`, and the `chat_id` of the group the bot is in. If
   **no**, drive [`scripts/feishu_bot_creator/`](../scripts/feishu_bot_creator/)
   in **drive mode**. The user logs in once (QR scan); after that
   the agent runs all 7 stages without further user involvement.
   Browser stays open the whole time — no re-launch between stages.

   ```bash
   cd scripts/feishu_bot_creator
   npm install                                          # one-time
   node create_feishu_bot.js login                      # one-time, user scans QR

   # Start drive (chromium opens once, stays open all 7 stages):
   node create_feishu_bot.js drive <bot-name> "<description>" \
     > /tmp/drive.log 2>&1 &

   # After each stage drive blocks waiting on .state/<bot>.cmd.
   # Read the log + state, then advance:
   echo next > .state/<bot-name>.cmd

   # If a stage looked wrong, redo it instead of advancing:
   echo "redo events" > .state/<bot-name>.cmd
   ```

   Per-stage details (what Playwright does, equivalent manual UI,
   how to recover) are in [`setup_feishu_bot.md`](setup_feishu_bot.md).

   After `publish`, read `App ID` + `App Secret` from the Feishu open
   platform's **Credentials & Basic Info** page. The user adds the
   bot to a group and tells you the group name; find the `chat_id`
   via `lark-cli im +chat-search --query "<group name>" --as user`.

2. **Pick host or Docker** — Docker is the simpler path (no Python on
   the host, just `docker compose`). Host is faster iteration but
   needs Python 3.10+, tmux, and the agent CLIs locally. Sections
   below cover both.

3. **Config** — write the credentials into `.env` (Docker) and the
   `chat_id` + agents into `claudeteam.toml`. `claudeteam init`
   generates a starter `claudeteam.toml` with three default agents
   (`manager` running Claude Code + `worker_cc` running Claude Code +
   `worker_codex` running Codex CLI) — keep them for a quick first
   smoke or edit before launch.

4. **Launch + verify** — `claudeteam up` then `claudeteam health`
   should be all green. From the Feishu group send `/health` and
   `@manager 你好`; manager should reply within ~30 s.

5. **If anything goes red**, see [Common failures](#common-failures)
   at the bottom — it covers Claude OAuth stale, container env not
   picked up, lark WebSocket drop, codex update prompt, etc.

---

## Prerequisites

| Requirement     | Version | Why                                                              |
| --------------- | ------- | ---------------------------------------------------------------- |
| Python          | 3.10+   | `pyproject.toml` pins `requires-python = ">=3.10"`               |
| tmux            | any     | every agent runs in its own tmux window                          |
| Node.js + npx   | 18+     | `lark-cli` is a node binary; `npx` is the install fallback       |
| At least one CLI| latest  | `claude` / `codex` / `kimi` / `gemini` / `qwen` (whichever your team uses) |
| Feishu (Lark)   | any     | enterprise app with `im:message` permission + WebSocket subscription |

**Feishu app setup**: easiest path is the bundled Playwright auto-creator
in [`scripts/feishu_bot_creator/`](../scripts/feishu_bot_creator/) — one
command to create + permission + subscribe + publish a bot. Manual
walkthrough at [`docs/setup_feishu_bot.md`](setup_feishu_bot.md).

Optional but recommended:
- `lark-cli` installed globally (`npm i -g @larksuite/cli`) — saves
  ~250 ms per invocation vs the `npx` fallback.

---

## Two deployment modes

| Mode    | When                                                | Notes |
| ------- | --------------------------------------------------- | ----- |
| **Host**   | macOS / Linux dev machine, you want fast iteration | `lark-cli` OAuth in your shell keychain, agent state under `./state/` |
| **Docker** | Headless / CI / multi-team isolation              | Image bundles Python + tmux + node; CLIs (claude/codex/...) you install yourself in a derived image, OR bind-mount the host binary |

Pick one and stick with it for a given Feishu chat — running both
against the same chat causes lark to silently split events between
the two subscribers. See `tests/scenarios/host_smoke.md` §8 for the
gory details.

---

## Host deploy (5 steps)

```bash
# 1. shell env (per terminal — add to ~/.zshrc / ~/.bashrc to persist)
cd /path/to/ClaudeTeam
export CLAUDETEAM_STATE_DIR="$PWD/state"   # else state goes to ~/.claudeteam
export LARK_CLI_NO_PROXY=1                 # required if HTTPS_PROXY is set
export CLAUDETEAM_LARK_SEND_AS=bot         # bot identity for headless smoke;
                                           # without it `say` defaults to user OAuth

# 2. install (editable, in a venv — PEP 668 means no bare pip on macOS Homebrew)
python3 -m venv .venv
source .venv/bin/activate
pip install -e .

# 3. bootstrap config (writes claudeteam.toml in cwd)
claudeteam init
$EDITOR claudeteam.toml                    # set chat_id + add agents

# 4. install slash hooks BEFORE up (claude-code caches them at pane spawn)
claudeteam install-hooks                   # writes .claude/commands/<name>.md

# 5. bring up the team
claudeteam up                              # tmux session + agents + router + watchdog
claudeteam health                          # verify everything green/yellow
```

**Tear down:**

```bash
claudeteam down       # stop panes + daemons, keep inbox/logs/cursor
claudeteam reset      # nuclear option: also wipes state
```

---

## Docker deploy

You don't need Python or `claudeteam` on the host — everything runs
in the container. The host only needs Docker + the host's
`~/.claude/.credentials.json` (extracted from macOS keychain on Mac
hosts) so the container can reuse your Claude OAuth.

```bash
# 1. fill credentials (gitignored)
cp .env.example .env
$EDITOR .env                    # FEISHU_APP_ID + FEISHU_APP_SECRET

# 2. macOS only — materialise Claude OAuth from keychain into a file
#    the container can bind-mount. Skip on Linux (file is already there).
mkdir -p ~/.claude
security find-generic-password -s "Claude Code-credentials" -w \
  > ~/.claude/.credentials.json

# 3. build the image and start the container
docker compose build
docker compose up -d

# 4. bootstrap config inside the container (output lands in ./team-data/)
docker compose exec --workdir /data claudeteam claudeteam init
$EDITOR team-data/claudeteam.toml    # set chat_id + agents

# 5. launch the team + verify
docker compose exec claudeteam claudeteam install-hooks
docker compose exec claudeteam claudeteam up
docker compose exec claudeteam claudeteam health
docker compose exec claudeteam tmux attach -t ClaudeTeam   # see panes; Ctrl+B d to leave
```

**Compose mounts (read `docker-compose.yml` for the full list):**

| Host path                              | Container path                          | Purpose |
| -------------------------------------- | --------------------------------------- | ------- |
| `./src/`                               | `/app/src/`                             | Hot-reload: edit on host, container picks up next invocation |
| `./team-data/`                         | `/data/`                                | `claudeteam.toml` + state survives `docker compose down` |
| `~/.lark-cli/config.json`              | `/root/.lark-cli/config.json`           | OAuth profile reused (file mount only — locks/ stays container-private to avoid host/container fcntl contention) |
| `~/.claude/.credentials.json`          | `/root/.claude/.credentials.json`       | Claude OAuth (RW so token refreshes persist back) |
| `~/.codex` / `~/.kimi`                 | `/root/.codex` / `/root/.kimi`          | Per-CLI credentials |

The base image deliberately does **not** bake in `claude` / `codex` /
`kimi` — each has its own auth and license. Derive from `claudeteam:dev`
and `RUN` the install you actually need, or bind-mount the host binary
into the container's `$PATH`.

---

## Configuration: `claudeteam.toml`

Single TOML file (Cargo-style) replaces the old `team.json` +
`runtime_config.json`. Comment-friendly, documented in-place by
`claudeteam init`'s template.

Key sections:

```toml
chat_id      = "oc_..."                       # Feishu group chat_id
lark_profile = ""                             # lark-cli profile name; "" = default
default_model = "opus"                        # fallback when an agent doesn't pin one

[team]
session = "ClaudeTeam"                        # tmux session name

[team.agents.manager]
cli = "claude-code"                           # claude-code | codex-cli | gemini-cli | kimi-code | qwen-code
role = "团队主管"                             # rendered into identity.md
model = "opus"
specialty  = ["调度", "审阅"]                 # optional — manager sees this in dispatch prompt
tone       = "稳重克制"                       # optional — biases LLM tone
notes      = "always answer in Chinese"       # optional — free-form prompt addendum
card_color = "blue"
publish_overrides = { worker_to_user = false } # per-agent override of [chat.publish]

[chat.publish]                                # who-talks-to-whom group filter
user_to_manager   = "always"                  # boss → manager (always lands)
manager_to_user   = "always"                  # manager → boss (always lands)
manager_to_worker = true                      # show dispatch cards in group
worker_to_manager = true                      # show worker progress in group
worker_to_user    = true                      # show worker completions in group
worker_to_worker  = true                      # show inter-worker pings in group
```

Defaults are wide open (everything visible) — flip individual keys
to `false` once the team's noise level needs trimming.

**Override precedence** (highest wins): `env` > `claudeteam.toml` > code default.
See `src/claudeteam/runtime/tunables.py` for the cascade.

---

## Agents talking to each other: `send` vs `say`

| Command | What it does | Reaches the worker's tmux pane? |
| ------- | ------------ | ------------------------------- |
| `claudeteam send <to> <from> <msg>` | Append a row to local `inbox.json` | **No** — only `claudeteam inbox <to>` reads it |
| `claudeteam say <agent> "<msg>" --to <role>` | Post into Feishu chat (subject to `[chat.publish]`) | Only if router relays it back |
| Feishu group → router → `deliver.apply` | Inbound chat → inbox row + tmux pane inject | **Yes** — the only path that wakes a worker |

**Always pass `--to`** on `say`. `--to user` = answering the boss;
`--to manager` = internal progress; `--to worker_<name>` = peer ping.
Skipping `--to` falls back to `user` for backwards compat but
defeats the publish filter.

---

## Multi-team isolation

Run multiple teams on one host by giving each its own state dir +
session name:

```bash
# team A
export CLAUDETEAM_STATE_DIR=/path/to/team-a/state
cd /path/to/team-a
claudeteam up   # session "TeamA"

# team B (different shell)
export CLAUDETEAM_STATE_DIR=/path/to/team-b/state
cd /path/to/team-b
claudeteam up   # session "TeamB"
```

Each team needs its **own Feishu app** (independent app_id/secret) —
sharing one app across teams causes credential leakage and event
routing conflicts. `claudeteam switch <team-dir>` emits the env
exports as one shell-evaluable line if you switch shells often.

---

## Slash commands (chat-side)

After `claudeteam install-hooks`, the manager pane recognises these:

| Slash | What it does |
| ----- | ------------ |
| `/help`     | Print the slash matrix card |
| `/team`     | All agents' status with ♥ heartbeat |
| `/health`   | Server CPU / memory / disk card |
| `/usage`    | ccusage wrapper for claude-code agents |
| `/tmux [agent] [N]` | Capture last N lines of an agent's pane |
| `/send <agent> <msg>` | Inject message into agent's pane |
| `/compact <agent>`    | Trigger LLM compact + scheduled re-identify |
| `/clear <agent>`      | Wipe pane history |
| `/stop <agent>`       | Kill pane (re-spawn with `claudeteam hire <agent>`) |
| `/peek <agent> [N]`   | Branded `tmux capture-pane` for the 5-min 巡视 cadence |

Boss can also send these from chat — they zero-LLM dispatch through
the router, no manager round-trip.

---

## Verifying the deploy

After `claudeteam up` returns green:

1. Send `/health` in the Feishu group → expect a card listing every
   agent + the router + watchdog as green.
2. Send `/team` → expect each agent's heartbeat fresh (♥ < 30 s).
3. Talk to the team in chat: `@manager` + a simple task. Manager
   should reply within 30 s, and if the task involves dispatch, you
   should see worker `say` cards land in the group.

If any of those fail, see "Common failures" below.

---

## Common failures

### "claude: not found" / "codex: not found" in pane

CLI adapter looks up the binary on `$PATH`. Spawned panes inherit
your launching shell's PATH. If you started a fresh terminal and
forgot to `source .venv/bin/activate`, the pane has no project venv.

### "Not logged in" in claude pane (macOS host)

Claude Code stores OAuth in macOS keychain. Per-agent home isolation
means each pane has its own `~/.claude/.credentials.json` snapshot,
which goes stale. Fix: `claudeteam down && claudeteam up` re-materialises
from keychain.

### Container `router` reports `lark-cli failed (rc=2)` and stalls

Catchup tried to use `--as user` but the container only has bot
OAuth. Make sure `docker-compose.yml` has `CLAUDETEAM_LARK_SEND_AS=bot`
in its `environment:` block (the bundled compose file ships with this).
Verify inside the container:

```bash
docker compose exec claudeteam env | grep CLAUDETEAM_LARK_SEND_AS
```

### Router silent stall (lark-cli alive but no events for 180s)

Router self-SIGTERMs via `_watch_subscribe_health` and watchdog
respawns. Usually transient (lark WebSocket dropped). If it's
constant, check whether another `lark-cli +subscribe` is running
elsewhere (host vs container, or a stale orphan):

```bash
ps -ef | grep -E "lark-cli.*subscribe" | grep -v grep
```

### Manager loops on the same anchored message after `claudeteam up`

Catchup replays everything newer than the cursor; the daemon also
keeps a `state/router.seen` dedup set persisted across restarts (auto
truncates at 5000 entries). If you still see duplicates, deleting
`state/router.seen` and bumping the cursor in `state/router.cursor`
forward to "now" makes the next catchup skip everything older.

### `worker_codex` shows "pane up but CLI not ready yet"

Codex CLI sometimes opens with an "update available" prompt that
blocks the ready marker. Fix:

```bash
tmux send-keys -t ClaudeTeam:worker_codex 3 Enter   # picks "Skip until next version"
claudeteam reidentify worker_codex
```

---

## Operator-friendly entry points

| Command | Purpose |
| ------- | ------- |
| `claudeteam up` / `down` | Bring team up / take it down |
| `claudeteam health` | One-shot status (binaries, env, tmux, daemons, cursor, memory) |
| `claudeteam team` | Each agent's state + ♥ heartbeat |
| `claudeteam peek <agent> [N]` | Pane snapshot for the 5-min check-in cadence |
| `claudeteam reidentify [<agent> \| --all]` | Re-inject identity.md (after prompt change) |
| `claudeteam usage [--days N]` | ccusage wrapper for claude-code agents |
| `claudeteam say <agent> "<msg>" --to <role>` | Post as agent into the chat |
| `claudeteam remember <agent> <kind> "<note>"` | Write durable memory (auto-injected on next pane wake) |
| `claudeteam switch <team-dir>` | Print env exports for multi-team UX |

`claudeteam --help` lists everything grouped by section.

---

## Where things live

```
src/claudeteam/
├── cli.py             single console-scripts entry; dispatch only
├── util.py            shared helpers (now_ms, atomic_write, env_str, ...)
├── commands/          one module per subcommand (~30-300 LOC each)
├── store/             local file-backed state (inbox, status, logs, tasks, memory)
├── agents/            CliAdapter base + per-CLI adapters + identity renderer
├── runtime/           config / paths / tmux / watchdog / pidlock / wake / lifecycle / tunables
└── feishu/            lark-cli wrapper + chat + router + slash + deliver + subscribe + catchup

tests/
├── unit/              per-module (stdlib runner)
├── integration/       end-to-end in-process
├── scenarios/         operator-run regression playbooks (markdown)
├── helpers.py         isolated_env() + run_cli() + attr/env patches + FakeProc
└── run.py             discovers + runs both unit/ and integration/
```

`CLAUDE.md` (project root) holds the building rules + active work
order — read it before making changes.
