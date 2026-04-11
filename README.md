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

The JSON to paste is the `scopes` object from `config/feishu_scopes.json` (without the `description` field):

```bash
python3 -c "import json; d=json.load(open('config/feishu_scopes.json')); print(json.dumps({'scopes': d['scopes']}, indent=2))"
```

### Step 3: Verify

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
8. **Every Claude instance must use `--name`** — `claude --dangerously-skip-permissions --name <agent名>`
