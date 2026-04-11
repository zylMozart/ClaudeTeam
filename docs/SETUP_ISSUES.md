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

## Bug 3: `config/feishu_scopes.json` format mismatch with batch import

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

## Enhancement: Add programmatic scope import via lark-cli

**Description:** Currently the only way to batch-add scopes is through the browser UI. It would be much better if `lark-cli` supported adding scopes programmatically, e.g.:
```bash
npx @larksuite/cli config add-scopes --file config/feishu_scopes.json
```
This would eliminate the need for browser interaction entirely in Phase 1.
