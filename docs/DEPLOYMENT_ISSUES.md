# Deployment Issues — 2026-04-29 Fresh Deploy Findings

A clean deploy of ClaudeTeam (clone → setup → launch → first user message) exposed the following bugs and friction points. Ordered by severity.

---

## P0 — Blocks core functionality

### 1. Lazy-mode agents never get Claude started (wake sends text to bare bash)

**File:** `src/claudeteam/messaging/router/daemon.py`, `scripts/start-team.sh`

**Symptom:** Agents in lazy-mode sit at a bare `zsh` shell. When the router or queue tries to deliver a message, it injects text like `你有来自 manager 的新消息` directly into the bash prompt. Result:

```
zsh: command not found: 你有来自
```

The agent never starts Claude Code, never processes any message.

**Root cause:** `start-team.sh` correctly creates lazy-mode panes with a banner, but `wake_on_deliver` in the router only injects the notification text — it does **not** spawn the CLI (`IS_SANDBOX=1 claude --dangerously-skip-permissions --model opus --name <agent>`) first. The wake path assumes the pane already has a running CLI, which is only true for non-lazy (whitelist) agents.

**Fix:** `wake_on_deliver` must detect that the pane is a bare shell (no CLI running), spawn the CLI first, wait for INSERT mode, then inject the message.

---

### 2. `detect_unsubmitted_input_text` false positive blocks all message delivery

**File:** `src/claudeteam/runtime/tmux_utils.py:144`

**Symptom:** After manager's Claude Code starts, the pane shows this in its visible history:

```
❯ IS_SANDBOX=1 claude --dangerously-skip-permissions --model opus --name manager
```

The `_INPUT_PROMPT_RE` regex matches `❯` followed by text, and `detect_unsubmitted_input_text` returns the entire command as "residual input." Every `inject_when_idle` call fails with `unsafe unsubmitted input`, and the queue delivery loop retries indefinitely (158 seconds in our case before the pane scrolled enough).

**Root cause:** The regex doesn't distinguish between a **shell prompt with an already-executed command** (visible in scrollback) and an **active CLI input field with unsubmitted text**. Both produce `❯ <text>`.

**Fix options:**
- Check whether a CLI process is running in the pane (e.g., `tmux list-panes -F '#{pane_current_command}'`) — if it's `claude`/`node`, the `❯ text` in scrollback is a completed shell command, not unsubmitted input.
- Only inspect the last 2–3 lines (not 8) for unsubmitted input, since the Claude Code UI puts the input prompt at the very bottom.
- Add the Claude startup command pattern to `_READY_PLACEHOLDERS` or a skip list.

---

### 3. `hire_agent.py` does not exist

**File:** `.claude/skills/hire/instructions.md` references `scripts/hire_agent.py`

**Symptom:** Steps 5 and 6 of the `/hire` skill call `python3 scripts/hire_agent.py setup-feishu <name>` and `python3 scripts/hire_agent.py start-tmux <name>`, but this script was never created.

**Impact:** `/hire` cannot complete. Manual workaround: create Bitable tables via `lark-cli base +table-create`, update `runtime_config.json`, and create tmux windows by hand.

**Fix:** Implement `scripts/hire_agent.py` with `setup-feishu` and `start-tmux` subcommands that:
- Create workspace table in Bitable
- Update `runtime_config.json` workspace_tables
- Create tmux window and spawn CLI (respecting lazy-mode)

---

## P1 — Serious friction / silent failures

### 4. `CLAUDETEAM_FEISHU_REMOTE=1` not set — all `say` commands fail by default

**File:** `scripts/feishu_msg.py`

**Symptom:** Every agent's first attempt to `say` (send to group chat) fails with:
```
❌ 远端发送默认关闭（local-only）；设置 CLAUDETEAM_FEISHU_REMOTE=1 后再发送
```

Agents must learn through trial-and-error to add the env var prefix.

**Root cause:** `feishu_msg.py` defaults to local-only mode for safety, but the identity.md templates and README instructions don't mention this requirement.

**Fix options:**
- Set `CLAUDETEAM_FEISHU_REMOTE=1` as a tmux environment variable in `start-team.sh` (via `tmux set-environment`)
- Or add it to the agent spawn command environment
- Or document it prominently in identity.md templates

---

### 5. User auth (`auth login`) is marked "optional" but required for group chat

**File:** `README.md` Phase 1 Step 3

**Symptom:** Without user auth, all `say` commands fail with `need_user_authorization`. The agents can write to Bitable (bot token) but cannot send messages to the Feishu group chat (requires user token via `im:message.send_as_user`).

**Root cause:** README says Step 3 is "optional, for most ClaudeTeam use cases" — but group chat messaging is the **primary** use case.

**Fix:** Make `auth login` a mandatory step in Phase 1. Or fall back to bot-identity messaging (`--as bot`) when user auth is unavailable, since the bot is already in the group.

---

### 6. Router crashes silently, no watchdog recovery observed

**Symptom:** The router process exited and left a bare shell prompt. During the ~10 minutes it was down, no automatic restart was observed despite the watchdog window existing.

**Root cause:** Not fully diagnosed. Possibly the watchdog checks the router by PID or process name and the check failed, or the watchdog itself had issues.

**Fix:** Investigate watchdog router monitoring logic. Consider adding a PID file or health check endpoint. At minimum, log router exits prominently.

---

## P2 — Paper cuts / DX friction

### 7. Submit key mismatch: single Enter doesn't submit in Claude Code INSERT mode

**Impact on automation:** When programmatically injecting text via `tmux send-keys`, a single `Enter` adds a newline to the input buffer. Claude Code requires Enter on an **empty line** to submit. The `_press_submit` function sends 3 keys (`Enter`, `C-m`, `C-j`) which works — but manual/ad-hoc injection from scripts or debugging misses this.

**Suggestion:** Document the submit sequence. Consider a helper script for manual message injection.

---

### 8. `pyproject.toml` declares `requires-python = ">=3.10"` but no enforcement

**Symptom:** macOS ships Python 3.9. `pip install -e .` fails because setuptools doesn't support editable installs from pyproject.toml on older pip. Workaround: `PYTHONPATH=src` bypasses the package install entirely.

**Fix:** Either lower the requirement to 3.8+ (if the code actually works on 3.9, which it did), or add a version check in `setup.py` / `start-team.sh` with a clear error message.

---

### 9. `setup.py` profile name mismatch warning is confusing for single-team deploys

**Symptom:** When the lark-cli profile name (`cli_a97a40b0ce78dcc0`) doesn't match the team.json session name (`claudeteam-test`), setup.py refuses to proceed unless `CLAUDE_TEAM_ACCEPT_DEFAULT_PROFILE=1` is set.

**Impact:** For single-team deploys (the common case), this is unnecessary friction. The warning is useful for multi-team setups but shouldn't block the default path.

**Fix:** Auto-accept for single-team deploys (no other teams detected), only warn/block when there's a real collision risk.
