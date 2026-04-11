# ClaudeTeam — AI Multi-Agent Team Framework

> Build and run a team of AI agents that collaborate through Feishu (Lark), powered by Claude Code.
> Each agent runs in its own tmux window, communicates via Feishu group chat, and is managed by a designated manager agent.

---

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

Tell the user they need a Feishu (Lark) app:

> To connect your AI team to Feishu, you need an app with messaging permissions.
>
> **Steps:**
> 1. Go to [Feishu Open Platform](https://open.feishu.cn) → Developer Console
> 2. Click "Create Custom App" (企业自建应用)
> 3. After creation, copy the **App ID** and **App Secret**
> 4. Add permissions (see `config/feishu_scopes.json` for full list):
>    - `base:app:create` (Create Bitable apps)
>    - `bitable:app` (Bitable read/write)
>    - `im:chat` (Chat management)
>    - `im:message` (Send & receive messages)
>    - `im:message:send_as_bot` (Send as bot)
>    - `im:resource` (Upload & download files)
> 5. Event subscription: enable "Long connection" mode, add `im.message.receive_v1`
> 6. Publish the app
>
> Once you have the App ID and App Secret, run:

```bash
npx @larksuite/cli config init
```

This configures lark-cli with your app credentials. Verify:

```bash
npx @larksuite/cli im +bot-info --as bot
```

If this prints bot info, credentials are valid. Proceed to Phase 2.

---

## Phase 2: Design Your Team

Ask the user what kind of team they want. Every team must have a `manager`. Generate `team.json`:

```json
{
  "session": "<team-name>",
  "agents": {
    "manager": {"role": "主管", "emoji": "🎯", "color": "blue"},
    "coder": {"role": "工程师", "emoji": "💻", "color": "green"}
  }
}
```

---

## Phase 3: Initialize Feishu Resources

```bash
python3 scripts/setup.py
```

This creates the Feishu group chat, Bitable tables, and workspace using lark-cli commands. Then create agent directories with identity files from `templates/`.

---

## Phase 4: Launch

```bash
bash scripts/start-team.sh
```

This starts tmux session with all agents, the message router (lark-cli WebSocket event stream), kanban sync, and watchdog.

---

## Phase 5: Enter Manager Mode

Read `agents/manager/identity.md`, then check inbox:

```bash
python3 scripts/feishu_msg.py inbox manager
```

---

## Communication Commands

```bash
# Send a direct message
python3 scripts/feishu_msg.py send <recipient> <sender> "<message>" [高|中|低]

# Post to group chat
python3 scripts/feishu_msg.py say <sender> "<message>"

# Check inbox
python3 scripts/feishu_msg.py inbox <your-name>

# Mark as read
python3 scripts/feishu_msg.py read <record_id>

# Update status
python3 scripts/feishu_msg.py status <your-name> <状态> "<description>"

# Log work
python3 scripts/feishu_msg.py log <your-name> 任务日志 "<what you did>"
```

---

## Architecture

```
Feishu Group Chat ←→ lark-cli WebSocket event stream
                         ↓
                   feishu_router.py (--stdin mode)
                         ↓
              ┌──────────┼──────────┐
              │          │          │
           manager    coder     writer   ... (tmux windows)
           (Claude)  (Claude)  (Claude)
              │          │          │
              └──────────┼──────────┘
                         ↓
                   feishu_msg.py (lark-cli wrapper)
                         ↓
                   Feishu Bitable (message inbox, status board, kanban)
```

---

## Rules for All Agents

1. **All communication through Feishu** — use feishu_msg.py commands
2. **Check inbox on startup** — first action after reading identity.md
3. **Update status after every state change**
4. **Log important milestones**
5. **Personal output → `agents/<name>/workspace/`**
6. **Shared output → `workspace/shared/`**
7. **Never create files in project root**
8. **Every Claude instance must use `--name`** — `claude --dangerously-skip-permissions --name <agent名>`
