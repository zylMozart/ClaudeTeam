# Setup Issues & Feedback

Bugs and friction points encountered during the ClaudeTeam setup process (2026-04-12).

---

## Bug 1: `im +bot-info --as bot` command does not exist [FIXED]

**Location:** README.md Phase 1 status check + `scripts/feishu_router.py:67`

**Description:** The README instructs the agent to verify lark-cli configuration with:
```bash
npx @larksuite/cli im +bot-info --as bot
```
This command does not exist in lark-cli v1.0.8. Running it returns a "help" listing of available `im` subcommands — `+bot-info` is not among them.

**Actual error:**
```
Error: unknown flag: --as
```
(When `--as` is passed at the `im` level instead of a subcommand level)

**Workaround used:** Verified connectivity with:
```bash
npx @larksuite/cli im +chat-search --query "test" --as bot --format json
```
A successful `{"ok": true, ...}` response confirms the CLI is properly configured.

**Suggested fix:** Replace `im +bot-info --as bot` with a command that actually exists, e.g.:
```bash
npx @larksuite/cli im +chat-search --query "test" --as bot
```

---

## Bug 2: Phase 1 is unnecessarily complex — `config init --new` already does 90% of the work [FIXED]

**Location:** README.md Phase 1 (lines 200-294)

**Description:** The README presents Phase 1 as a 6-step manual process:
1. Open browser, create app, copy credentials
2. Run `config init --new`
3. Manually add 10+ permissions one by one
4. Manually configure event subscription (long connection + im.message.receive_v1)
5. Manually publish the app
6. Verify

In reality, `npx @larksuite/cli config init --new` handles **all of this automatically**:
- Opens a browser auth page
- User scans QR code to login
- User creates a new app OR selects an existing one
- Automatically adds core permissions (im:message:send_as_bot, docx:document:readonly)
- Automatically sets up event subscriptions (im.message.receive_v1, reactions, message_read)
- Automatically publishes the app

The only remaining step is batch-importing the additional permissions from `config/feishu_scopes.json`.

**Impact:** What should take ~1 minute takes 5-10 minutes with the current instructions. The "Option A / Option B" choice is unnecessary friction.

**Suggested fix:** Rewrite Phase 1 as:
1. Run `config init --new` → scan QR → create/select app → confirm (automatic)
2. Batch import remaining scopes from `config/feishu_scopes.json`
3. Verify

---

## Bug 3: `config/feishu_scopes.json` format mismatch with batch import [FIXED]

**Location:** `config/feishu_scopes.json`

**Description:** The file contains a `"description"` field at the top level:
```json
{
  "description": "飞书应用所需权限列表 — 初始化时导入或供用户参考",
  "scopes": { ... }
}
```
The Feishu batch import dialog expects this exact format:
```json
{
  "scopes": {
    "tenant": [...],
    "user": [...]
  }
}
```
The `description` field doesn't cause an error (it's ignored), but it's confusing because the file can't be used as-is for batch import — the agent has to strip the `description` field first.

**Suggested fix:** Either remove the `description` field or add a comment in the README noting the import format.

**Fix applied:** Removed the `description` field from `config/feishu_scopes.json`. The file is now in the exact shape Feishu's batch import expects and can be pasted with a plain `cat config/feishu_scopes.json` — no Python stripping needed. Updated README Phase 1 Step 2 to reflect this.

---

## Bug 4: Monaco editor in batch import dialog is hard to programmatically interact with

**Location:** Feishu Developer Console → Permissions & Scopes → Batch import/export scopes

**Description:** The batch import dialog uses a Monaco editor that doesn't expose `window.monaco` globally. Standard approaches (setting textarea value, using Monaco API) don't work. The only way to set content is:
1. Click the hidden Monaco textarea with `force: true`
2. `Cmd+A` to select all
3. `Backspace` to delete
4. Use clipboard API + `Cmd+V` to paste

This is a Feishu platform issue, not a ClaudeTeam issue, but it affects automated setup flows.

---

## Bug 5: `setup.py` does not generate group chat invite link [FIXED]

**Location:** `scripts/setup.py`, `create_chat_group()` function

**Description:** After creating the Feishu group chat, `setup.py` saves the `chat_id` but never generates a share/invite link. The user has no way to join the group chat and interact with the team — which is the entire point of the system.

**Fix applied:** Added `im chats link` API call after chat creation to generate a permanent invite link. The link is saved in `runtime_config.json` as `share_link` and printed to stdout.

---

## Bug 6: `feishu_router.py` uses non-existent `im +bot-info` for self-echo filtering [FIXED]

**Location:** `scripts/feishu_router.py:64`, `init_bot_id()` method

**Description:** The router daemon calls `im +bot-info --as bot` to get the bot's `open_id` for filtering out the bot's own messages. Since this command doesn't exist, the bot ID is never set, and self-echo filtering is disabled. This can cause the bot to react to its own messages in some scenarios.

**Fix applied:** Replaced with `im chat.members get` API call to list group members and identify the bot member from the chat.

---

## Bug 7: `setup.py` does not create Manager identity file — Manager cannot distribute tasks [FIXED]

**Location:** `scripts/setup.py`

**Severity:** Critical

**Description:** The setup flow creates `team.json` with a `manager` entry, then runs `setup.py` and `start-team.sh`. But **nobody creates `agents/manager/identity.md`**. The `/hire` skill creates identity files for other agents (scheduler, coder, etc.), but Manager is the first agent — it's created directly by `setup.py` + `start-team.sh`, never going through `/hire`.

Without `identity.md`, the Manager agent:
- Does not know its team members
- Does not know how to use `send` to distribute tasks to agents' inboxes
- Only uses `say` to post in the group chat, which other agents **cannot see**
- Result: **the entire team is deaf** — Manager talks in group chat, but no agent receives any task

**Impact:** This is effectively a **total system failure**. The team appears to be running (all tmux windows active), but no work can be distributed because the coordination channel (inbox via `send`) is never used.

**Fix applied:** Added `init_manager_identity()` to `setup.py` that:
1. Creates `agents/manager/` directory structure
2. Copies `templates/manager.identity.md` (with fallback to built-in template)
3. Appends current team member list
4. Generates `core_memory.md` with team roster
5. Runs automatically during `setup.py`, before team launch

---

## Enhancement: Add programmatic scope import via lark-cli

**Description:** Currently the only way to batch-add scopes is through the browser UI. It would be much better if `lark-cli` supported adding scopes programmatically, e.g.:
```bash
npx @larksuite/cli config add-scopes --file config/feishu_scopes.json
```
This would eliminate the need for browser interaction entirely in Phase 1.

---

## Bug 8: README 没有指引 agent 如何确认「该用哪个 App」 [FIXED]

**Location:** README.md Phase 1 + Quick Status Check (lines 168-196)

**Severity:** Medium (causes wasted minutes on first setup)

**Description:** When the status check reports `lark-cli: CONFIGURED`, the agent blindly trusts whatever App is currently in `/root/.lark-cli/config.json`. If the user hands over an App ID/Secret at the start of the conversation, the agent has no documented way to decide:

1. Is the user's credential the one *already* configured (no-op)?
2. Is it a *different* App the user wants to switch to (reconfigure)?
3. Is the currently-configured App actually the one that has scopes/publishing done (keep it), while the user-provided App is a freshly created empty one (reject it)?

In a real session this caused a full round-trip:
- `config show` said App A was configured
- User provided App B credentials → agent did `config remove` + `config init` → switched to App B
- `auth login --domain all` ran against App B → **"no permission"** error, because App B had no scopes published
- User then provided App A credentials → agent had to switch back → login worked

**Suggested fix:** In the Quick Status Check, if `lark-cli` is CONFIGURED *and* the user has just handed over credentials, the agent should:
1. Read `config show` and compare appId.
2. If they match → proceed with existing config.
3. If they differ → **ask the user explicitly** which App has permissions/publishing completed, rather than silently switching.

Additionally, the README should note that `config init` with a freshly created (unpublished) App is not enough — scopes must be published via the permissions page before `auth login --domain all` will succeed.

**Fix applied:** Added a "Handling user-provided credentials" section to the Quick Status Check in README.md. It tells the agent to run `config show` first, compare App IDs, and ask the user explicitly which App has scopes + publishing completed before switching.

---

## Bug 9: `auth login --domain all` confusion — users think App ID/Secret is sufficient [FIXED]

**Location:** README.md Phase 1 Step 3 (lines 248-256)

**Severity:** Low (documentation clarity)

**Description:** After handing over App ID + Secret, users reasonably assume the setup is "fully automatic" and are surprised that `auth login --domain all` still requires a QR scan. The README says "This enables your agents to manage your calendar, create documents, and query tasks." but does not explain *why* a second authentication step is required given that the App is already authenticated.

**Root cause:** App ID/Secret authenticates the **bot** (tenant identity). User-level APIs (calendar, personal docs, private tasks, contact search) require a **user token** which can only be obtained via OAuth device flow — this is a Feishu platform design, not a ClaudeTeam limitation.

**Suggested fix:** Rewrite Phase 1 Step 3 to explicitly state:
> **Why scan again?** App ID/Secret gives the *bot* permission. Feishu's permission model requires a *separate* user token for features that act on your personal data (your calendar, your docs). This scan is a one-time consent to let your agents act on your behalf — it is unrelated to the App configuration itself.

Also add a note: "If you only need the core chat/Bitable/agent coordination loop, you can skip this step — the team will run fine without user identity."

**Fix applied:** Rewrote Phase 1 Step 3 in README.md to explicitly explain bot vs user identity ("the App ID / Secret gives the bot permission... Feishu's permission model requires a separate user token for features that act on personal data"), note that the step is optional for the core loop, and instruct the agent to run the command itself and extract the device-flow URL for the user rather than asking the user to run `auth login` manually.

---

## Bug 10: `setup.py` crashes with `NameError: PROJECT_ROOT is not defined` [FIXED]

**Location:** `scripts/setup.py:197` (`init_manager_identity()`)

**Severity:** Critical — aborts setup after Feishu resources are already created, leaving the user in an inconsistent state (Bitable + chat created, but no `runtime_config.json` saved and no manager identity).

**Description:** `setup.py` uses `PROJECT_ROOT` inside `init_manager_identity()` (lines 197 and 212) but never imports it from `config.py`. The import line only brings in `AGENTS, CONFIG_FILE, TMUX_SESSION, save_runtime_config, get_lark_cli`.

**Actual error:**
```
Traceback (most recent call last):
  File "scripts/setup.py", line 347, in <module>
    main()
  File "scripts/setup.py", line 328, in main
    init_manager_identity()
  File "scripts/setup.py", line 197, in init_manager_identity
    mgr_dir = os.path.join(PROJECT_ROOT, "agents", "manager")
                           ^^^^^^^^^^^^
NameError: name 'PROJECT_ROOT' is not defined
```

**Impact cascade:** Because this crashes *after* `create_bitable()`, `create_*_table()`, and `create_chat_group()` succeed but *before* `save_runtime_config()`, the user is left with:
- Orphaned Bitable and chat on the Feishu side
- No `runtime_config.json` on disk
- Re-running `setup.py` will happily create *another* set of Bitable + chat, orphaning the first set forever

**Fix applied:** Added `PROJECT_ROOT` to the import line in `setup.py`:
```python
from config import AGENTS, CONFIG_FILE, TMUX_SESSION, PROJECT_ROOT, save_runtime_config, get_lark_cli
```

**Suggested additional fix:** Make `setup.py` robust against mid-flight crashes by persisting `runtime_config.json` incrementally (after each successful step), so re-runs can skip already-completed steps. Right now the atomicity is: "all or nothing, and nothing means duplicated resources."

---

## Bug 11: `claude --dangerously-skip-permissions` refuses to start as root — whole team silently dead [FIXED]

**Location:** `scripts/start-team.sh` (lines 46, 52), `scripts/hire_agent.py` (`cmd_start_tmux`), `scripts/docker-entrypoint.sh` (lines 56, 61), README.md line 419.

**Severity:** Critical — this is the most brutal silent failure in the whole pipeline. The tmux session appears healthy (all windows exist, look like they booted), but no agent is actually running Claude Code. The init messages sent via `tmux send-keys` get typed into a bare bash prompt, producing error cascades like `-bash: 你是团队的: command not found` — and the whole system appears to be "running" while doing nothing.

**Description:** Claude Code CLI (≥ 2.1.x) refuses to start with `--dangerously-skip-permissions` when the effective UID is 0 (root). The exact error is:

```
--dangerously-skip-permissions cannot be used with root/sudo privileges for security reasons
```

Since ClaudeTeam's typical deploy target is a VM or container running as root (and uses `--dangerously-skip-permissions` by design for autonomous operation), every `tmux send-keys "claude --dangerously-skip-permissions --name <agent>"` call exited immediately, dropping back to bash. The subsequent init message `tmux send-keys "你是团队的 <agent>..."` was then typed into *bash*, which of course reports `command not found` for every line.

Because none of the agent Claude processes ever started, but the tmux windows and watchdog did, the team looked alive but was entirely non-functional. `feishu_msg.py inbox` calls never happened, `status` updates never happened, and group chat messages were silently dropped even after router was running.

**Fix applied:** Prefix every Claude launch with `IS_SANDBOX=1`:

```bash
IS_SANDBOX=1 claude --dangerously-skip-permissions --name <agent>
```

Updated in all 4 spawn sites:
- `scripts/start-team.sh` (line 46, 52)
- `scripts/hire_agent.py` `cmd_start_tmux()`
- `scripts/docker-entrypoint.sh` (line 56, 61)
- README.md "Rules for All Agents" line 419 (documentation)

The `IS_SANDBOX=1` env var tells Claude Code "you're in a controlled sandbox, the root-check can be relaxed" and is the supported way to run `--dangerously-skip-permissions` in containers / VMs.

**Suggested additional fix:** `start-team.sh` and `hire_agent.py` should probe the tmux window 3-5 seconds after launching Claude to verify the Claude UI actually loaded (grep for the `⏵⏵ bypass permissions on` banner in `tmux capture-pane`). If the banner is missing, abort with a clear error instead of silently proceeding to send init messages to a dead shell.

---

## Bug 12: `lark-cli event +subscribe` in router pipeline is missing `--as bot` — router crashes immediately [FIXED]

**Location:** `scripts/start-team.sh:64`, `scripts/docker-entrypoint.sh:67`, `scripts/watchdog.py:22-26`, `scripts/feishu_router.py:329-333`.

**Severity:** Critical — router never starts, meaning no messages ever reach agents from the Feishu group chat.

**Description:** The `event +subscribe` command pipes NDJSON events into `feishu_router.py --stdin`. All four spawn sites for this pipeline omit `--as bot`, so lark-cli auto-detects identity as `user` (or falls back to `default-as`), and the event API rejects it:

```json
{
  "ok": false,
  "identity": "user",
  "error": {
    "type": "validation",
    "message": "resolved identity \"user\" (via auto-detect or default-as) is not supported, this command only supports: bot\nhint: use --as bot"
  }
}
```

`lark-cli` then exits, the pipe closes, `feishu_router.py` reaches EOF and exits cleanly. Watchdog detects the crash and restarts — but the restart hits the same error. After 3 consecutive failures, watchdog gives up, posts a "router 连续 3 次重启失败,需人工介入" alert to manager's inbox, and stops restarting. From that point, **no group-chat message ever reaches any agent**, which matches exactly what the user reported.

**Fix applied:** Added `--as bot` to the `event +subscribe` command in all 4 places:
- `scripts/start-team.sh:64`
- `scripts/docker-entrypoint.sh:67`
- `scripts/watchdog.py` `_lark_event_cmd` string
- `scripts/feishu_router.py` self-start mode `subprocess.Popen` args

**Suggested additional fix:** Have `feishu_router.py` parse the first few lines from stdin and, if they contain `"ok": false` + `identity not supported`, print a clear "ROUTER CANNOT START: add --as bot to the upstream event +subscribe" error with an exit code watchdog will surface prominently, rather than looking like a clean EOF.

---

## Bug 13: `tmux kill-session` leaves orphaned `lark-cli event +subscribe` pipelines [FIXED]

**Location:** General tmux teardown path used by Phase 4 / manual restart.

**Severity:** Medium — pollutes process table and can race with newly-launched router.

**Description:** When the user (or the agent) runs `tmux kill-session -t <session>`, tmux kills the shell processes in each window, but `lark-cli event +subscribe | python3 feishu_router.py --stdin` is a pipeline: the bash pipeline process exits, but the inner `npx @larksuite/cli event +subscribe` Node process and its child `lark-cli` binary get reparented to init/PID 1 and keep running, still holding a WebSocket subscription to Feishu events.

When `start-team.sh` is re-run immediately afterward, there are now *two* `event +subscribe` subscribers for the same bot, and events may arrive on either one. Worse, the orphans from the pre-fix run (no `--as bot`) were still alive in this session's process table even after `kill-session`, leading to confusing state when trying to verify the fresh router was working.

**Repro in this session:**
```
$ tmux kill-session -t ecom
$ ps aux | grep "event +subscribe" | grep -v grep
root 3068198 ... bash -c npx @larksuite/cli event +subscribe --event-types im.message.receive_v1 ...
root 3068707 ... bash -c npx @larksuite/cli event +subscribe --event-types im.message.receive_v1 ...
root 3070546 ... bash -c npx @larksuite/cli event +subscribe --event-types im.message.receive_v1 ...
# Three orphan pipelines from previous runs, all still holding subscriptions
```

**Suggested fix:** Before `tmux kill-session` (and in a `stop-team.sh` wrapper if added), `pkill -f "event \+subscribe"` and `pkill -f "feishu_router.py"` first. Or track router PIDs in `runtime_config.json` and target them specifically. Long-term: move `feishu_router.py` into a systemd-like supervisor (or a `tmux new-window` that runs a single parent python that manages the lark-cli subprocess itself — that way `tmux kill-session` cleanly terminates everything).

**Fix applied:** Added an orphan-cleanup block to the top of `scripts/start-team.sh`. On every start, before creating the new tmux session, it scans for leftover `event +subscribe --event-types im.message.receive_v1` processes and `feishu_router.py` processes and kills them:

```bash
ORPHAN_COUNT=$(pgrep -f "event +subscribe --event-types im.message.receive_v1" | wc -l)
if [ "$ORPHAN_COUNT" -gt 0 ]; then
  echo "🧹 清理 $ORPHAN_COUNT 个 router 孤儿进程..."
  pkill -f "event +subscribe --event-types im.message.receive_v1" 2>/dev/null || true
  pkill -f "feishu_router.py" 2>/dev/null || true
  sleep 1
fi
```

This prevents the "two routers competing for events" scenario and avoids confusing process tables during debugging.

---

## Bug 14: `chat.members get` times out after 15 seconds — self-echo filtering permanently disabled [FIXED]

**Location:** `scripts/feishu_router.py` `init_bot_id()` (lines 72-76).

**Severity:** Low — non-fatal, but the router logs a scary warning on every start.

**Description:** `init_bot_id()` calls `im chat.members get --params ... --as bot --page-all --format json` with a 15-second subprocess timeout to discover the bot's own `open_id` (so router can filter out the bot's own messages from the event stream). In this deployment, that call reliably times out:

```
⚠️ 获取 bot info 异常: Command '[...chat.members get...]' timed out after 15 seconds，自回声过滤将不可用
```

Possible causes:
1. `--page-all` iterates all pages; for a just-created chat with few members this should be instant, so the timeout suggests a `lark-cli` / network issue with this specific endpoint
2. The bot was added with `--set-bot-manager` but may not appear in `chat.members get` as a regular member (bot members sometimes live in a separate namespace)

**Impact:** Self-echo filtering is disabled, meaning if the bot ever receives its own outbound messages in the event stream, they'll be treated as user messages. In practice `im.message.receive_v1` already filters bot messages upstream, so this is belt-and-suspenders, but it generates a spurious warning every restart.

**Suggested fix:**
1. Bump the timeout to 30-60s, OR
2. Use a different API to get the bot's `open_id` — e.g., `auth.tenant_access_token.internal` + `im.chats.members.bot` specific endpoint, OR
3. Cache the `bot_open_id` in `runtime_config.json` after first successful lookup so subsequent router restarts don't re-probe.

**Fix applied:** Combined fixes 1 + 3 in `scripts/feishu_router.py` `init_bot_id()`:
- Timeout bumped from 15s to 40s.
- On first successful lookup, the resolved `bot_open_id` is written back to `runtime_config.json`. On subsequent router restarts, the cached value is used and the lookup is skipped entirely (`🤖 Bot open_id (cached): ou_...`).
- Also clarified the warning message: `chat.members.get` in Feishu often does not return the bot as a "member" (bots are counted separately via `bot_count` in `chats get`), so failing this lookup is not a bug in ClaudeTeam — it's a Feishu API quirk. Upstream `im.message.receive_v1` already filters bot self-messages, so the self-echo filter here is belt-and-suspenders. The new warning explicitly states this: "不影响上游事件过滤,自回声防护降级".

---

## Bug 15: Concurrent agent init triggers Bitable write rate-limit [FIXED]

**Location:** `scripts/feishu_msg.py` status updates during simultaneous `start-team.sh` startup.

**Severity:** Low — transient, agents self-recover.

**Description:** When `start-team.sh` launches all agent windows back-to-back with only `sleep 1` between init messages, multiple Claude agents simultaneously run `feishu_msg.py status <agent> 进行中 "初始化完成..."`, which translates to Bitable `record-batch-create` calls. At 4 agents, Feishu returned `{"ok": false, "identity": "bot", ...}` for the `analyst` and `product` status updates (both reported the error in their tmux output while still completing their self-init successfully from local state).

**Impact:** First-boot state table shows slightly stale data for some agents until the next status update. The agents themselves are fine.

**Suggested fix:**
1. In `start-team.sh`, stagger agent init messages with `sleep 3` instead of `sleep 1`, OR
2. In `feishu_msg.py`, add a retry-with-exponential-backoff wrapper around `_lark_base_create` for rate-limit responses, OR
3. Have `setup.py` pre-populate the state table with "待命" rows for all agents (already does this for the initial team), and rely on first-batch `status 进行中` being serialized by the router rather than parallel.

**Fix applied:** Staggered agent init in `scripts/start-team.sh` — changed the inter-agent `sleep 1` to `sleep 2.5`, giving each agent ~2.5 seconds before the next Bitable write hits. For a 4-agent team, total startup time increases ~6 seconds but rate-limit errors disappear.

---

## Bug 16: `config init --app-id ... --app-secret-stdin` does NOT configure event subscriptions — router connects but receives zero events [PARTIALLY FIXED]

**Location:** Quick Status Check / Phase 1 of README.md (when user hands over credentials for an existing App) + `scripts/start-team.sh` router pipeline assumption.

**Severity:** Critical — looks like everything works (WebSocket connects, `--as bot` accepted, permissions published), but `im.message.receive_v1` events never arrive. User messages sent into the Feishu group chat are silently dropped on Feishu's side and never reach the router, even when the user is a valid chat member and the bot has all required scopes.

**Description:** Feishu apps have two separate configuration surfaces that look similar but are independent:
1. **Permissions & Scopes** (权限管理) — what APIs the bot/user can call
2. **Events & Callbacks** (事件订阅) — which events the app subscribes to AND the delivery mode (webhook URL vs long connection)

The `lark-cli config init --new` command sets up **both**, including enabling long-connection mode and subscribing to a default set of events (`im.message.receive_v1`, message-read, reactions). But `lark-cli config init --app-id X --app-secret-stdin` (non-interactive credentials-only init) only stores the App ID/Secret in `~/.lark-cli/config.json` — it makes no API calls to the Feishu developer console and does **not** touch event subscriptions.

As a result, if the user hands over credentials for an existing App (common scenario: "here's my App I already created"), and the agent runs `config init --app-id ... --app-secret-stdin` to switch, the lark-cli config is valid but the App's event subscription state is whatever it was before — likely "no events subscribed" if the App was created manually via the web console.

**Symptoms observed in this session:**
- Router/WebSocket: `Connected. Waiting for events...` ✅
- `event +subscribe --as bot`: accepted ✅
- `im +messages-send --as user --chat-id <our-chat>`: returns `message_id` ✅
- User is a valid member of the chat ✅
- All scopes published ✅
- **Events received after 10+ seconds:** `0` ❌
- Manager inbox via router: empty; nothing ever arrived

**Why this wasn't Bug 11/12:** those two bugs broke the *local* pipeline (Claude wouldn't start; `--as bot` missing crashed the event stream upstream). Bug 16 is different: the local pipeline is healthy but Feishu's event delivery side never sends anything because the App isn't subscribed to the event type on the server side.

**Fix applied (in this session):** Ran `lark-cli config init --new`, scanned the QR code, chose **"使用已有应用"**, and selected the existing App ID (`cli_a9518e4e1d39dbc0`). The CLI pushed the event subscription (`im.message.receive_v1`) and long-connection mode to the App's server-side config and re-published. Immediately after, the same `event +subscribe` + `im +messages-send --as user` probe delivered `received 1 events` within milliseconds.

**Suggested fix:**
1. **README Quick Status Check** — when detecting "lark-cli is CONFIGURED but user handed over credentials," add a verification step that probes the event stream with a 5-second timeout: send a test message via `--as user` and confirm at least one event arrives. If zero events: fall through to running `config init --new` on the existing App, don't just trust the credentials.
2. **`scripts/feishu_router.py`** — on startup, if no events arrive for N seconds, print a clear banner: `⚠️ Router connected but 0 events in <N>s. This usually means the App is missing the im.message.receive_v1 event subscription. Run 'npx @larksuite/cli config init --new' and choose "Use Existing App" to fix.`
3. **`lark-cli` itself** — expose an `lark-cli config push-events --app-id X` subcommand that adds event subscriptions programmatically, so `config init --app-id ... --app-secret-stdin` followed by `config push-events` can fully configure without browser interaction.
4. **Document the split** — in README Phase 1, explicitly mention the two independent surfaces (permissions vs events) and warn that batch-importing scopes ≠ subscribing to events. The two look similar in the developer console but live on different pages (`/auth` vs `/event`).

**Fix applied:**
1. **`scripts/feishu_router.py`** now has an `_event_watchdog` thread that fires 45s after router start. If `first_event_at` is still `None` at that point, it prints a 6-line banner pointing directly at Bug 16 and telling the user exactly which command to run (`config init --new` → "使用已有应用"). This turns a silent failure into a loud one — any future user hitting this bug will see the fix instructions in their router pane within a minute of starting the team.
2. **README.md Quick Status Check → "Handling user-provided credentials"** — added an explicit warning and a 5-second event-stream probe bash snippet that the agent should run after swapping credentials. If the probe returns 0 events, the agent knows to run `config init --new` immediately instead of hours of false-positive "everything's green" debugging.

**Still suggested (not applied — requires external changes):**
- Suggestion 3 above (`lark-cli config push-events` subcommand) requires changes to lark-cli itself and is out of scope for this repo.
- Long-term: `setup.py` could do its own event-stream probe right after creating the Bitable + chat, and fail fast with the Bug 16 banner if events aren't flowing — this would catch the issue before any agent is even launched.
