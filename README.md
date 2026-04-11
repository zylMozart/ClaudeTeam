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

| Requirement     | Version    | Check                                    |
| --------------- | ---------- | ---------------------------------------- |
| macOS or Linux  | —          | —                                        |
| Python          | 3.8+       | `python3 --version`                      |
| Node.js         | 18+        | `node --version`                         |
| tmux            | any        | `tmux -V`                                |
| Claude Code CLI | latest     | `claude --version`                       |
| Feishu account  | Enterprise | [open.feishu.cn](https://open.feishu.cn) |

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

## FAQ

**Q: Does this work with other LLMs?**
A: Currently built for Claude Code. The harness could theoretically work with other CLI LLM tools.

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
npx @larksuite/cli im +bot-info --as bot 2>/dev/null && echo "CONFIGURED" || echo "NOT CONFIGURED"
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

---

## Phase 1: Configure Feishu App

Ask the user which setup mode they prefer:

> **How would you like to set up the Feishu app?**
>
> **Option A (Recommended):** I'll open the browser and guide you step by step. You just click a few buttons and paste the credentials here.
>
> **Option B (Manual):** I'll give you all the steps at once and you do them yourself.

### Option A: Guided Setup (Recommended)

#### Step 1: Create the App

Open the browser:

```bash
open "https://open.feishu.cn/app" 2>/dev/null || xdg-open "https://open.feishu.cn/app" 2>/dev/null || echo "Please open: https://open.feishu.cn/app"
```

Tell the user:

> I've opened the Feishu Developer Console. Please:
> 1. Click **"Create Custom App"** (创建企业自建应用)
> 2. Name it **"ClaudeTeam"** (or anything you like)
> 3. Copy the **App ID** and **App Secret** and paste them here.

Wait for credentials.

#### Step 2: Configure lark-cli

```bash
npx @larksuite/cli config init --new
```

Then verify:

```bash
npx @larksuite/cli im +bot-info --as bot
```

#### Step 3: Add Permissions

Open the permissions page:

```bash
open "https://open.feishu.cn/app" 2>/dev/null || xdg-open "https://open.feishu.cn/app" 2>/dev/null
```

Tell the user to open their app settings → **Permissions & Scopes**, and add:

- `bitable:app` — Bitable read/write
- `base:app:create` — Create Bitable apps
- `base:table:create` — Create tables
- `base:field:create` — Create fields
- `base:record:create` — Create records
- `im:chat` — Chat management
- `im:message` — Send & receive messages
- `im:message:send_as_bot` — Send as bot
- `im:message:receive_as_bot` — Receive message events
- `im:resource` — Upload & download files

#### Step 4: Event Subscription

Tell the user to go to **Event Subscription** in app settings:

1. Change method to **"Long connection"** (长连接)
2. Add event: **im.message.receive_v1**
3. Save

#### Step 5: Publish

Tell the user to go to **Version Management**, create a version, and publish.

#### Step 6: Verify

```bash
npx @larksuite/cli im +bot-info --as bot
```

If successful, proceed to Phase 2.

### Option B: Manual Setup

Print all steps at once:

1. Visit https://open.feishu.cn → Create Custom App → Copy App ID and Secret
2. Run `npx @larksuite/cli config init --new`
3. Add permissions: bitable:app, base:app:create, base:table:create, base:field:create, base:record:create, im:chat, im:message, im:message:send_as_bot, im:message:receive_as_bot, im:resource
4. Event subscription: Long connection + im.message.receive_v1
5. Publish the app
6. Verify: `npx @larksuite/cli im +bot-info --as bot`

Wait for the user to confirm completion.

---

## Phase 2: Design Your Team

Ask the user what kind of team they want. Present options:

> **What kind of AI team do you want?**
>
> **A — Minimal (3):** manager + coder + tester
> **B — Standard (5):** manager + architect + coder + tester + writer
> **C — Custom:** Tell me the roles you need.
>
> What should we name this team?

Every team must have a `manager`. Generate `team.json`:

```json
{
  "session": "<team-name>",
  "agents": {
    "manager": {"role": "主管", "emoji": "🎯", "color": "blue"},
    "coder": {"role": "工程师", "emoji": "💻", "color": "green"}
  }
}
```

Then create agent directories with identity files from `templates/`.

---

## Phase 3: Initialize Feishu Resources

```bash
python3 scripts/setup.py
```

Creates: Feishu group chat, Bitable tables (inbox, status, kanban, workspace per agent), saves IDs to `scripts/runtime_config.json`.

---

## Phase 4: Launch

```bash
bash scripts/start-team.sh
```

Starts: tmux session with all agents, message router (lark-cli WebSocket), kanban sync, watchdog.

Tell the user:

> Your team is running!
> - **Feishu group**: Send messages to talk to your agents
> - **tmux**: `tmux attach -t <session>` to view agent windows

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
8. **Every Claude instance must use `--name`** — `claude --dangerously-skip-permissions --name <agent名>`
