# ClaudeTeam

[中文](docs/README_CN.md) | [English](README.md)

> *Harness your Claude Code*

Your Claude Code agent keeps polluting its own context. Fix A, break B. Fix B, break A.

You don't need a smarter agent. You need a Harness — isolated agents, parallel execution, zero cross-contamination.

**ClaudeTeam: your first Harness.** One repo, multiple Claude Code agents, coordinated through Feishu.

*2025, Prompt Engineering. 2026, Harness Engineering.*

### Screenshots

**Feishu Group Chat — Control your AI team in real-time**

<table>
  <tr>
    <td><img src="docs/media/example/feishu_example1.jpg" width="200" /></td>
    <td><img src="docs/media/example/feishu_example2.jpg" width="200" /></td>
    <td><img src="docs/media/example/feishu_example3.jpg" width="200" /></td>
    <td><img src="docs/media/example/feishu_example4.jpg" width="200" /></td>
    <td><img src="docs/media/example/feishu_example5.jpg" width="200" /></td>
  </tr>
</table>

**tmux Backend — Claude Code agents running in parallel**

<p><img src="docs/media/example/tmux_example.png" width="800" /></p>

---

## What It Does

ClaudeTeam turns Claude Code into a multi-agent system. Each agent runs in its own tmux window, has its own identity and memory, and communicates with teammates through a Feishu group chat. A manager agent coordinates the team — assigns tasks, reviews output, and reports to you.

```
You (Feishu group chat)
  ↕
Router (real-time WebSocket events from Feishu)
  ↕
┌──────────┬──────────┬──────────┐
│ Manager  │ Agent A  │ Agent B  │  ← tmux windows, each running Claude Code
│(assigns) │(executes)│(executes)│     (you define the roles)
└──────────┴──────────┴──────────┘
  ↕
Feishu Bitable (message storage, status board, kanban)
```

---

## Features

- **One-command setup** — Clone, open Claude Code, the Agent guides you through everything
- **Real-time collaboration** — Agents communicate through Feishu group chat with colored message cards
- **Autonomous agents** — Each agent has its own identity, memory, workspace, and task queue
- **Team management** — `/hire` and `/fire` slash commands to add or remove agents on the fly
- **Watchdog** — Crashed agents auto-restart, with notifications in Feishu
- **Kanban board** — Task status synced to Feishu Bitable in real-time
- **Extensible** — Add any role you need: architect, tester, researcher, ops, educator...

---

## Prerequisites

For the **Quick Start** path (host-native, guided by `claude`):

| Requirement     | Version    | Check                                    |
| --------------- | ---------- | ---------------------------------------- |
| macOS or Linux  | —          | —                                        |
| Python          | 3.8+       | `python3 --version`                      |
| Node.js         | 18+        | `node --version`                         |
| tmux            | any        | `tmux -V`                                |
| Claude Code CLI | latest     | `claude --version`                       |
| Feishu account  | Enterprise | [open.feishu.cn](https://open.feishu.cn) |

For the **Docker Deployment** path, you only need:

| Requirement     | Version    | Check                                    |
| --------------- | ---------- | ---------------------------------------- |
| Docker          | 20.10+     | `docker --version`                       |
| Docker Compose  | v2         | `docker compose version`                 |
| Feishu account  | Enterprise | [open.feishu.cn](https://open.feishu.cn) |

Python / Node.js / tmux / Claude Code CLI all live inside the image and don't need to be on the host.

---

## Quick Start

```bash
git clone https://github.com/zylMozart/ClaudeTeam.git
cd ClaudeTeam
claude
```

That's it. Claude Code reads this file and auto-guides you through:

1. **Creating a Feishu app** — Agent opens the browser for you, you just click and paste credentials
2. **Designing your team** — Agent asks what roles you need
3. **Initializing Feishu resources** — fully automatic
4. **Launching the team** — fully automatic

The whole process takes about 5 minutes.

> **Heads up.** The "fully automatic" claim only holds when Phase 1 uses `npx @larksuite/cli config init --new`, which scans a QR code, *creates* a fresh Feishu App, AND pushes event subscriptions to the server in one shot. If you instead supply an existing App's `App ID` / `App Secret` directly (e.g. you already created it via the Feishu web console), you must complete two extra manual steps in the Feishu developer console: **batch-import the scopes** and **add the `im.message.receive_v1` event subscription**, then publish a new version. Both are covered in [Phase 1](#phase-1-configure-feishu-app) below — read those steps if `claude` doesn't auto-handle them for you.

---

## Docker Deployment (alternative, hands-off)

If you want a fully containerized deploy with no shared state on the host, use this flow instead of `claude`:

```bash
git clone https://github.com/zylMozart/ClaudeTeam.git
cd ClaudeTeam

# 1. Credentials live in project-local .env (gitignored).
cp .env.example .env
$EDITOR .env                        # fill FEISHU_APP_ID / FEISHU_APP_SECRET

# 2. Define the team you want.
$EDITOR team.json                   # session name + agents (see templates/)

# 3. Bind-mount target must exist before first run, otherwise Docker turns it
#    into a directory.
touch scripts/runtime_config.json

# 4. Build the image.
docker compose build

# 5. One-shot init: container creates the Bitable / group chat / inbox tables
#    and writes scripts/runtime_config.json. Exits when done.
docker compose run --rm team init

# 6. Start the team for real (manager + workers + router + watchdog).
docker compose up -d
```

Why this flow:

- **Feishu credentials never touch the host.** They live in `.env` and only get materialized inside the container's writable layer at startup. Nothing is written to `~/.lark-cli` on the host. Multiple ClaudeTeam deployments on the same host can't see each other's Feishu identities.
- **Claude Code credentials are shared via bind mount** (`~/.claude/.credentials.json` + `~/.claude.json`). Same Anthropic account in multiple deployments is the normal case, so this is fine. If you'd rather use an API key, set `ANTHROPIC_API_KEY` in `.env` — the bind mounts become optional.
- **The whole `scripts/` directory is bind-mounted** so you can edit Python scripts on the host and they take effect on container restart, no rebuild needed.

**Before this works** you still have to do the two manual Feishu console steps once (scopes batch-import + event subscription). See [Phase 1](#phase-1-configure-feishu-app) below — those steps apply to both Quick Start and Docker Deployment.

To interact with the team:

```bash
docker compose logs -f                                  # follow startup logs
docker compose exec team tmux attach -t <session>       # attach to tmux
docker compose down                                     # stop
```

The Feishu group chat invite link is printed at the end of `docker compose run --rm team init` — open it on your phone, join the group, and chat with the manager.

---

## Usage

### Talking to Your Team

Send messages in the Feishu group chat. The manager distributes work. @mention a specific agent to talk to them directly.

### Viewing the Team

```bash
tmux attach -t <session-name>    # enter the tmux session
Ctrl+B, n / p                    # next / previous agent window
Ctrl+B, d                        # detach (leave running)
```

### Managing Agents

From within Claude Code (as manager):

```
/hire <role-name> "<role-description>"
/fire <role-name>
```

---

## Running Multiple Teams on One Host

You can run N teams side-by-side on the same machine — each team has its own project directory, its own `team.json`, its own tmux session, its own Feishu group chat. **But you have to choose how they share the Feishu identity:**

### Option A — Shared Feishu App (simpler, recommended for hobby / dev)

All teams use the **same** Feishu App (same App ID / App Secret). Each team has its own group chat; the router filters incoming events by `chat_id` so messages never cross team boundaries.

**Setup:**
```bash
# First team — normal flow
cd ~/project/teamA && claude     # follows README Phase 1 & 2 as usual

# Second team — setup.py detects the shared profile and asks you to confirm
cd ~/project/teamB && claude
# ...when setup.py prints the "profile conflict" warning, rerun it with:
CLAUDE_TEAM_ACCEPT_SHARED_PROFILE=1 python3 scripts/setup.py
```

**Pros:** Zero extra Feishu setup. One App, one permissions page, one publish step.
**Cons:** All teams share a single bot identity. If App Secret leaks, every team is compromised. Depends on the router's `chat_id` event filter being correct.

### Option B — Separate Feishu App per team (real isolation)

Each team gets its own Feishu App, saved as a **named lark-cli profile**.

**Setup:**
```bash
cd ~/project/teamB

# 1) Create a new Feishu App under a named profile (scan QR, click through)
npx @larksuite/cli config init --new --name teamB

# 2) Run setup.py with the profile override — it writes lark_profile=teamB
#    into runtime_config.json so all subsequent scripts use the right identity.
LARK_CLI_PROFILE=teamB python3 scripts/setup.py

# 3) Start the team as usual
bash scripts/start-team.sh
```

`start-team.sh`, `feishu_router.py`, `watchdog.py` all read `lark_profile` from `runtime_config.json` and pass `--profile <name>` to every `lark-cli` call, so the two teams never share credentials, events, or bot state.

**Pros:** True identity isolation. A leaked secret or misconfigured bot in team A can't touch team B.
**Cons:** Double the Feishu admin work (two Apps, two permission pages, two publish steps).

### Deciding which one you need

| | Option A | Option B |
|---|---|---|
| Different teams owned by the same person | ✅ | overkill |
| Different teams owned by different people | ❌ | ✅ |
| Testing + staging + prod on one box | ❌ (easy to confuse) | ✅ |
| Single-user hobby projects | ✅ | overkill |
| You're unsure | start with A, migrate to B if needed | |

Under the hood, both options rely on the `chat_id` filter in `feishu_router.py` — Option A as the primary isolation mechanism, Option B as defense-in-depth.

### Docker: isolating containers, volumes, and networks

`docker-compose.yml` intentionally **does not** set `container_name:`. A fixed container name is globally unique, so the second `docker compose up` on the same host would see "container `claudeteam` already exists" and happily recreate it — wiping the first team. Instead, Compose auto-names containers as `<project>-team-1`, where `<project>` comes from `COMPOSE_PROJECT_NAME` (or the current directory basename if unset).

For multi-team hosts, tie the project name to each team's `session` so it shows up clearly in `docker ps`:

```bash
# Preferred: use the shipped script, it exports COMPOSE_PROJECT_NAME=claudeteam-<session> for you
bash scripts/docker-deploy.sh

# Manual path: set it yourself before every docker compose call
cd ~/project/teamA
export COMPOSE_PROJECT_NAME=claudeteam-$(python3 -c 'import json; print(json.load(open("team.json"))["session"])')
docker compose up -d
docker compose exec team tmux attach -t "$(python3 -c 'import json; print(json.load(open("team.json"))["session"])')"
docker compose down
```

The same `COMPOSE_PROJECT_NAME` must be set for every subsequent `docker compose ...` invocation in that shell — otherwise Compose falls back to the directory basename and can't find your containers/volumes. If you bounce between teams frequently, consider a small shell alias per team or put `export COMPOSE_PROJECT_NAME=claudeteam-<session>` at the bottom of the team's `.env` and source it.

Top-level `volumes:` and `networks:` declared in `docker-compose.yml` are already auto-prefixed by the project name, so this change is the single knob that isolates *everything* at once.

---

## Multi-CLI Adapter

ClaudeTeam supports **heterogeneous teams** — different agents can run different CLI tools (Claude Code, Kimi, Gemini CLI, Codex CLI, Qwen Code) in the same team.

### How it works

`scripts/cli_adapters/` contains a Python ABC (`CliAdapter`) with per-CLI implementations. Each adapter defines:
- `spawn_cmd` — the shell command to start the CLI in a tmux pane
- `ready_markers` — strings that indicate the CLI UI is ready
- `busy_markers` — strings that indicate the agent is busy (spinner, "Thinking", etc.)
- `process_name` — the `/proc/<pid>/comm` name for liveness checks

### Supported CLIs

| CLI | Adapter name | Install |
|---|---|---|
| Claude Code | `claude-code` (default) | `npm i -g @anthropic-ai/claude-code` |
| Kimi Code | `kimi-code` | `uv tool install kimi-cli` |
| Gemini CLI | `gemini-cli` | `npm i -g @anthropic-ai/gemini-cli` |
| Codex CLI | `codex-cli` | `npm i -g @openai/codex` |
| Qwen Code | `qwen-code` | `npm i -g qwen-code` |

### Configuring per-agent CLI

Add a `"cli"` field to `team.json` (omit for default `claude-code`):

```json
{
  "agents": {
    "manager":  { "role": "Team Lead", "cli": "claude-code" },
    "writer":   { "role": "Writer",    "cli": "kimi-code" }
  }
}
```

### Adding a new adapter

Create `scripts/cli_adapters/my_cli.py` (~40 lines), implement the 4 abstract methods, and register it in `__init__.py`.

---

## Community

Join our WeChat group for discussions, feedback, and feature requests!

<img src="docs/media/wechat-community-qr.png" width="300" alt="WeChat Group QR Code" />

> Note: The QR code refreshes every 7 days. If expired, please open an Issue to request a new one.

---

## FAQ

**Q: Does this work with other LLMs?**
A: Yes! The multi-CLI adapter system supports Claude Code, Kimi, Gemini CLI, Codex CLI, and Qwen Code. See the "Multi-CLI Adapter" section above.

**Q: Can I use Slack/Discord instead of Feishu?**
A: Not out of the box. The messaging layer is Feishu-specific.

**Q: How many agents can I run?**
A: Tested up to 10. 8GB RAM handles 5 comfortably.

**Q: Is `--dangerously-skip-permissions` safe?**
A: Required for autonomous operation. Only use in trusted environments.

**Q: What if an agent crashes?**
A: The watchdog auto-restarts it and notifies you in Feishu.

**Q: How much does it cost?**
A: ClaudeTeam is free and open source. Costs come from Claude API usage. Feishu free tier and lark-cli are both free.

---

## Contributing

Contributions welcome! See [docs/CONTRIBUTING.md](docs/CONTRIBUTING.md).

## License

[MIT](LICENSE)

---

<!-- CLAUDE:START — Everything below is for Claude Code automated setup -->

> **Below this line is for Claude Code.**
> Human users do not need to read past this point — Claude Code handles everything automatically.
> When you run `claude` in this project, it reads this file and follows the instructions below.

---

## Agent Instructions

You are the setup assistant for ClaudeTeam. Run the status checks below, follow the decision tree, and guide the user through each phase.

## Quick Status Check

**Run these checks every time you start. They determine what to do next.**

### Check 1: Is lark-cli configured?

```bash
npx @larksuite/cli im +chat-search --query "test" --as bot 2>/dev/null | grep -q '"ok": true' && echo "CONFIGURED" || echo "NOT CONFIGURED"
```

### Check 2: Does `team.json` exist?

```bash
test -f team.json && echo "EXISTS" || echo "MISSING"
```

### Check 3: Is the tmux session running?

```bash
python3 -c "import json; print(json.load(open('team.json'))['session'])" 2>/dev/null | xargs -I{} tmux has-session -t {} 2>/dev/null && echo "RUNNING" || echo "NOT RUNNING"
```

### Decision Tree

| lark-cli | `team.json` | tmux session | Action |
|----------|------------|--------------|--------|
| NOT CONFIGURED | — | — | Go to **Phase 1** |
| CONFIGURED | MISSING | — | Go to **Phase 2** |
| CONFIGURED | EXISTS | NOT RUNNING | Go to **Phase 4** |
| CONFIGURED | EXISTS | RUNNING | Go to **Phase 5** |

### Handling user-provided credentials

If the user hands over an App ID / App Secret at the start of the conversation, **do not silently trust whatever lark-cli already has configured**. Run this check first:

```bash
npx @larksuite/cli config show 2>/dev/null | grep -o 'cli_[a-z0-9]*' | head -1
```

Compare the returned `appId` with the user's:

- **Match** → keep the existing config, proceed with the status check above.
- **Different** → ask the user explicitly which App has permissions + publishing completed:
  > I see lark-cli is currently configured with `<existing>`, but you provided `<new>`. Which one has the scopes batch-imported and published? If I switch to the new one and it's a freshly-created empty App, `auth login` will fail with "no permission".

  Only after the user confirms should you `config remove` + `config init --app-id ... --app-secret-stdin`.

This avoids a wasted round-trip where the agent switches to an empty App and the user has to re-hand the original credentials.

**⚠️ Critical — `config init --app-id ... --app-secret-stdin` does NOT configure event subscriptions.** It only writes credentials. If the App was created manually (not via `config init --new`), it likely has no events subscribed on the server side — meaning router will connect, `--as bot` will succeed, but `im.message.receive_v1` events will never arrive. After setting credentials, always probe the event stream:

```bash
# 5-second probe: send a test message as user, count events received
timeout 8 bash -c '
  npx @larksuite/cli event +subscribe --event-types im.message.receive_v1 --as bot 2>/dev/null > /tmp/evt_probe.out &
  EVT_PID=$!
  sleep 2
  npx @larksuite/cli im +messages-send --chat-id <EXISTING_CHAT_ID> --text "probe" --as user > /dev/null 2>&1
  sleep 5
  kill $EVT_PID 2>/dev/null
  wc -l < /tmp/evt_probe.out
'
```

If the line count is 0, the App is missing event subscriptions. Run `config init --new` and have the user pick **"使用已有应用"** + the existing App ID to push event subs to the server.

---

## Phase 1: Configure Feishu App

`lark-cli config init --new` handles everything: app creation, permissions, event subscription, and publishing — all in one command.

### Step 1: Run config init

```bash
npx @larksuite/cli config init --new
```

This opens a browser page. Tell the user:

> A browser window has opened. Please:
> 1. **Scan the QR code** with Feishu to log in
> 2. Choose **"Create"** (to make a new app) or **"Use Existing App"** (if you already have one)
> 3. Click **"Confirm"** — that's it!

Wait for the CLI to print `OK: 应用配置成功!` — this means the app is created, permissions and events are configured, and the app is published.

### Step 2: Add remaining permissions

`config init` adds basic scopes, but ClaudeTeam needs more (Bitable, chat management, etc.). Batch-import them:

Open the app's Permissions page:

```bash
# Get the App ID from lark-cli config
APP_ID=$(npx @larksuite/cli config show 2>/dev/null | grep -o 'cli_[a-z0-9]*' | head -1)
open "https://open.feishu.cn/app/${APP_ID}/auth" 2>/dev/null || xdg-open "https://open.feishu.cn/app/${APP_ID}/auth" 2>/dev/null || echo "Please open: https://open.feishu.cn/app/${APP_ID}/auth"
```

Tell the user:

> I've opened the Permissions page. Please:
> 1. Click **"Batch import/export scopes"**
> 2. Select all text in the editor, delete it
> 3. Paste the JSON I'll give you, then click **"Next, Review New Scopes"** → **"Add"**

`config/feishu_scopes.json` is already in the exact format Feishu's batch import expects — paste it as-is:

```bash
cat config/feishu_scopes.json
```

The shipped file is intentionally minimal: 4 umbrella scopes (`bitable:app`, `im:chat`, `im:message`, `im:resource`) plus three explicit record-level scopes (`base:record:read/create/update`). Why mix umbrellas and fine-grained? Because Feishu's umbrellas are inconsistent: `im:message` does cover its sub-permissions like `im:message:send_as_bot`, but `bitable:app` does **NOT** cover `base:record:*` operations — record CRUD has to be granted explicitly. If you ever see `99991672 Access denied. One of the following scopes is required: [some:scope]` at runtime, add `some:scope` to `feishu_scopes.json`, re-import, and re-publish.

**⚠️ Don't forget to publish.** After adding the scopes, click **"Create version & Publish"** in the top-right corner of the developer console. Without publishing, every API call will keep failing with `99991672`.

### Step 2.5: Subscribe to events (only if you didn't use `config init --new`)

If Phase 1 Step 1 used `npx @larksuite/cli config init --new` and you scanned the QR code, **skip this step** — the CLI already pushed event subscriptions to the Feishu server side as part of app creation.

If instead you supplied an existing `App ID` + `App Secret` (e.g. via `config init --app-id ... --app-secret-stdin`, or you created the App manually in the Feishu web console), then your App **does not yet have any events subscribed on the server side**. Symptom: router starts fine, `chat-search --as bot` returns `ok: true`, but the router's "45 seconds 0 events" warning fires and messages from your group never reach any agent.

**To fix in the Feishu developer console:**

```bash
APP_ID=$(npx @larksuite/cli config show 2>/dev/null | grep -o 'cli_[a-z0-9]*' | head -1)
open "https://open.feishu.cn/app/${APP_ID}/event" 2>/dev/null \
  || echo "Open: https://open.feishu.cn/app/${APP_ID}/event"
```

In the page that opens:

1. Sidebar → **"事件与回调" → "事件订阅"**
2. Set **传输方式 / Transport** to **"长连接" (long-polling / WebSocket)**, NOT webhook (ClaudeTeam's router uses `lark-cli event +subscribe` over WebSocket)
3. Click **"添加事件" / Add Event**, search for `im.message.receive_v1` (display name: "**接收消息 v2.0**" or "**Receive Message**") → Add
4. Save
5. **Re-publish a new version** (top-right corner). Without re-publishing, the new event subscription is staged but not live.

**To verify it actually works:** restart the router (or the whole container) and watch for the "45 seconds 0 events" warning. If it stays silent past 45 seconds and you can see incoming messages flowing through, you're good.

### Step 3: User login (enables calendar, docs, tasks, contacts)

**Why a second authentication step?** The App ID / App Secret you already provided gives the *bot* permission to act. Feishu's permission model requires a *separate* user token for features that act on the user's personal data (their calendar, their docs, their private tasks, contact search). This scan is a one-time consent to let agents act on the user's behalf — it is unrelated to the App configuration itself, and cannot be skipped by providing more credentials.

**Is this step optional?** Yes, for most ClaudeTeam use cases. The core loop (group chat, Bitable kanban, agent coordination) runs entirely on the bot identity. Only skip this if the user explicitly wants calendar / docs / personal-task automation. When in doubt, ask the user whether they need those features before running this step.

If proceeding, run:

```bash
npx @larksuite/cli auth login --domain all
```

This prints a device-flow verification URL. **Do not ask the user to run the command themselves — you run it, then extract the URL from the output and give it to them to open.** Tell the user:

> Please open this link in your browser and authorize with your Feishu account: `<url>`. Authorization code: `<code>`.

Wait for `OK: 登录成功!`. If you see `no permission`, the scopes from Step 2 were not published yet — go back and publish, then retry.

### Step 4: Verify

```bash
npx @larksuite/cli im +chat-search --query "test" --as bot
```

If it returns `{"ok": true, ...}`, you're good. Proceed to Phase 2.

---

## Phase 2: Design Your Team

### Step 1: Understand the project

Ask the user to describe their project:

> **Tell me about your project.** What are you building?
> I'll analyze what you need and suggest the right team.

### Step 2: Propose a team

Based on the user's description, analyze what roles are needed. Consider:
- Frontend work? → coder
- Backend/API work? → backend or coder
- System design needed? → architect
- Testing needed? → tester
- Documentation/content? → writer
- Research needed? → researcher
- Deployment/infra? → devops
- Platform-specific needs? → specialist roles

Propose a team. Example:

> Based on your project, I recommend:
>
> | Role | Responsibility |
> |------|---------------|
> | 🎯 manager (me) | Coordinate, review, report to you |
> | 🏗️ architect | System design, tech decisions |
> | 💻 coder | Implementation |
> | 🧪 tester | Quality assurance |
>
> Should I start building this team? You can also add or remove roles.

### Step 3: Get confirmation

**⚠️ MANDATORY: Wait for explicit user confirmation before proceeding.**
Do NOT create any agents without the user saying "yes" / "ok" / "go ahead" or similar.

The user may:
- **Approve as-is** → proceed
- **Modify** (add/remove roles) → adjust proposal, show updated list, ask for confirmation again
- **Say "just manager for now"** → minimal path (skip /hire, only manager)

### Step 4: Build the team

After confirmation, ask the user for a team name, then:

1. Create `team.json` with only `manager`:
```json
{"session": "<team-name>", "agents": {"manager": {"role": "主管", "emoji": "🎯", "color": "blue"}}}
```

2. Run `python3 scripts/setup.py` to initialize Feishu resources

3. Run `bash scripts/start-team.sh` to start tmux (starts manager + router + watchdog)

4. For each additional role the user confirmed, execute `/hire`:
```
/hire architect 系统架构师，负责技术方案设计
/hire coder 软件工程师，负责代码实现
/hire tester 测试工程师，负责质量保障
```

5. **Generate and send the Feishu group chat invite link to the user.** This is the final deliverable — without it the user cannot interact with the team.

```bash
# Get chat_id from runtime config
CHAT_ID=$(python3 -c "import json; print(json.load(open('scripts/runtime_config.json'))['chat_id'])")

# Generate a permanent share link
npx @larksuite/cli im chats link \
  --params "{\"chat_id\":\"${CHAT_ID}\"}" \
  --data '{"validity_period":"permanently"}' \
  --as bot --format json
```

Extract the `share_link` from the response and send it to the user. Tell them:

> Here is your team's Feishu group chat link. Click to join, then you can send messages to control your AI team.

**⚠️ MANDATORY: Do NOT skip this step. The invite link is the primary way the user interacts with their team.**

6. Once all agents are hired and the link is delivered, enter Phase 5.

### Minimal Path

If the user says "just manager" or "no team yet":
1. Create team.json with only manager
2. Run setup.py + start-team.sh
3. Generate and send the group chat invite link to the user
4. Tell the user: "Team is running with just me (manager). Use `/hire` anytime to add teammates."

---

## Phase 5: Enter Manager Mode

Read `agents/manager/identity.md`, then check inbox:

```bash
python3 scripts/feishu_msg.py inbox manager
```

Follow the manager workflow: check inbox → process messages → assign tasks → monitor progress.

---

## Communication Commands Reference

```bash
python3 scripts/feishu_msg.py send <recipient> <sender> "<message>" [高|中|低]
python3 scripts/feishu_msg.py say <sender> "<message>"
python3 scripts/feishu_msg.py inbox <your-name>
python3 scripts/feishu_msg.py read <record_id>
python3 scripts/feishu_msg.py status <your-name> <状态> "<description>"
python3 scripts/feishu_msg.py log <your-name> 任务日志 "<what you did>"
```

## Rules for All Agents

1. **All communication through Feishu** — use feishu_msg.py commands
2. **Check inbox on startup** — first action after reading identity.md
3. **Update status after every state change**
4. **Log important milestones**
5. **Personal output → `agents/<name>/workspace/`**
6. **Shared output → `workspace/shared/`**
7. **Never create files in project root**
8. **Every Claude instance must use `--name`** — `IS_SANDBOX=1 claude --dangerously-skip-permissions --name <agent名>`. The `IS_SANDBOX=1` prefix is required when running as root (common in VMs / containers); without it, `--dangerously-skip-permissions` refuses to start and the tmux window falls back to a bare bash shell where init messages get typed into the shell instead of Claude.
