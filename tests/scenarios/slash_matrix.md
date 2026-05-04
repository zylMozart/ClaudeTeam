# Slash matrix (rebuild/minimal)

Canonical slash-command coverage for smoke runs on this branch. Mirrors
the structure of `origin/feat/messaging-fixes-block1:tests/smoke/slash_matrix.md`
but reflects rebuild's actual 11-command set, the v2 card schema (R159),
and the env-driven tenant token bootstrap (R161) that makes container
deploys work without macOS keychain.

A real Feishu user message in the bound chat is the canonical trigger.
**Slash commands must start at the very first character** of the user
message — `@bot /team` does NOT trigger; just `/team`. Fake `subscribe.process_lines`
events are acceptable for handler coverage but must be labeled that way
in the run log; they don't prove the lark-cli +subscribe long-poll path.

## Read-only commands

| Command | Expected group result | Pass criteria | Fail criteria |
| --- | --- | --- | --- |
| `/help` | Card listing every supported `/<cmd>` | Lists all 11: /help /team /health /usage /tmux /send /compact /recall /forget /stop /clear | Missing command, plain text instead of card |
| `/team` | Card with `<emoji> **<agent>**: <brief>` per agent + tally | Each team agent rendered with one of (💤 idle / 🔄 working / ⏸ lazy / ⚠ awaiting / 🛑 down / 🔘 unknown). Header green if all healthy, yellow if any ⚠/🛑/❌. Lazy agents show ⏸ not 🛑. | Missing agent, wrong session, lazy agent shown as 🛑 (R129/R144 regression) |
| `/health` | Card wrapping `claudeteam health` text in fenced code block | v2 markdown renders the fence as a real grey-bg code block (R159). Header green when output has no ❌/⚠️, yellow otherwise. | Triple backticks visible as text (R159 regression), missing sections |
| `/usage [view]` | Card wrapping `claudeteam usage` (default `daily`) | ccusage table for claude-code agents, plus per-CLI note for codex/kimi/qwen. v2 markdown preserves table alignment. | Empty body, npx-not-found error swallowed |
| `/tmux [agent] [N]` | Card with last N (default 10, max 2000) lines of the agent's pane | Body is a real code block — monospace, indentation preserved. Defaults to manager when no agent. | Triple backticks as literal text (R159 regression), wrong pane, truncation past N |
| `/recall <agent> [N] [--kind K]` | Card listing agent's recent memory entries oldest-first | Each row `[ts] [kind] content (ref=...)`. With `--kind`, scans full window and trims to N matches (R141). Unknown kind → soft warn in card subtitle. | Empty when entries exist, kind filter returns 0 for valid known kinds, fenced/list rendering broken |

## State-mutating commands

These mutate live CLI state — only fire on smoke-safe targets, capture
before/after pane state.

| Command | Expected | Risk | Pass |
| --- | --- | --- | --- |
| `/send <agent> <msg>` | tmux send-keys + Enter into the agent pane | Bypasses lazy-wake / inbox; raw injection. Probe with disposable text only. | Pane receives once, no duplicate, no shell pollution |
| `/compact [agent]` | `/compact` injection then 45s-deferred identity reinject | Long-running CLI compact in target pane | One compact request lands, identity reread fires after settle |
| `/stop <agent>` | `C-c` to the agent pane | Interrupts active work | Probe interrupted, pane usable |
| `/clear <agent>` | `/clear` then re-init prompt (rehire shape) | Loses CLI conversation context | One /clear + one init message, no shell pollution |
| `/forget <agent> [--kind K] --yes` | Deletes memory file (or kind slice) | Loses durable memory; `--yes` gated (R112) | Without --yes → grey card with reissue hint; with --yes → row count + red header |

## Message routing (in-band, no slash)

Slash commands are zero-LLM router-level intercepts. The other path
the router exercises is **classify → deliver → inbox + tmux inject**
for user messages and agent-to-agent traffic. These tests prove the
full lark-subscribe → router → store/local_facts + runtime/tmux loop
is wired end-to-end.

### Boss → manager (default route)

| # | Trigger | Expected |
| --- | --- | --- |
| R1 | Boss types `开发一个登录页` (no @-mention, no `[`-prefix) in the bound chat | `manager`'s inbox.json gains one row (sender=`user`, content=raw text); `manager` pane receives the text + Enter via lifecycle.wake_if_dormant; manager status flips to 进行中 |
| R2 | Boss types `[boss] /team` (manager-style sender prefix) | Router pre-strips `[boss]` and dispatches `/team` as a slash; bot reply lands in chat. **Regression**: `[boss] /team` must NOT route to manager as a normal user message (round A2 B1) |

### Boss → @-mentioned worker

| # | Trigger | Expected |
| --- | --- | --- |
| R3 | `@worker_cc 看一下 README` | `worker_cc`'s inbox + pane receive the body (sender=`user`); `manager` is NOT touched |
| R4 | `@worker_cc @worker_kimi 都过来` | Both workers' inboxes + panes get the body; tally in router log shows `targets=[worker_cc, worker_kimi]` |
| R5 | `@unknown_agent hi` (typo'd name) | Falls through to `default_target` (manager) since no recognised mention; manager inbox gets the row including the literal `@unknown_agent` text |

### Boss → broadcast

| # | Trigger | Expected |
| --- | --- | --- |
| R6 | `@team 全员同步进度` | Every team agent EXCEPT the sender (boss/user has no team agent → all agents) gets one inbox row + pane inject. Decision.action=BROADCAST in router log |
| R7 | `全体注意，今晚 18:00 review` | Same as R6 — `全体X` matches the broadcast prefix |
| R8 | `@team` only (no body text) | Empty-body broadcast still fans out; agents see an empty inbox row (downstream behaviour is by design — sender intent unclear, but routing is correct) |

### Agent → agent (peer messaging)

These run from within an agent's pane, exercising `claudeteam send`:

| # | From pane | Command | Expected |
| --- | --- | --- | --- |
| P1 | manager | `claudeteam send worker_cc manager "查 auth 模块"` | `worker_cc.inbox` gains a row (from=manager, to=worker_cc); pane receives the text. `manager` pane unchanged. |
| P2 | worker_cc | `claudeteam send manager worker_cc "auth 用 bcrypt"` | `manager.inbox` gains a row (from=worker_cc, to=manager); manager pane receives it. **Regression**: do not let `from=worker_cc` accidentally drop as cross-team or self-talk. |
| P3 | worker_cc | `claudeteam send worker_kimi worker_cc "看一下 token 过期处理"` | `worker_kimi.inbox` row (from=worker_cc, to=worker_kimi); worker_kimi pane receives it. Manager unchanged — peer-to-peer doesn't mirror to manager. |
| P4 | manager | `claudeteam say manager "进展同步" --card` | Group chat receives a v2-markdown card with manager's blue header; the text appears in the chat (visible to boss + all workers via the chat UI, but NO inbox rows are written for that path — `say` is "speak in chat", not "deliver to inbox"). |

### Reporting & visibility

The boss-asked "manager sees worker messages":

| # | Setup | Expected |
| --- | --- | --- |
| V1 | worker_cc runs `claudeteam say worker_cc "完成 auth 模块" --card` | Card lands in chat (green header per worker_* convention). Manager pane does NOT auto-receive; manager's responsibility is to see it via chat or via `claudeteam peek worker_cc`. |
| V2 | manager runs `claudeteam peek worker_cc 30` from its pane | Output is the last 30 lines of worker_cc's pane buffer; equivalent to `/tmux worker_cc 30` from chat but without round-trip latency. |
| V3 | All-staff report flow: boss `@team 报告状态`; each worker replies via `claudeteam say <self> "<status>" --card`; manager runs `claudeteam team` to verify each worker upserted status | Every worker's status shows 进行中 or 已完成 in `claudeteam team`. Chat shows a card per worker with their role-color header. Boss can scan the chat OR `/team` to see the tally. |

### Inbox audit

| # | Step | Expected |
| --- | --- | --- |
| I1 | Boss `@worker_cc test`; then in worker_cc pane `claudeteam inbox worker_cc` | One unread row with sender=user, content=test. |
| I2 | worker_cc runs `claudeteam read <local_id>` then `claudeteam inbox worker_cc --unread` | The acknowledged row is gone from unread list. |
| I3 | Boss spams 5 `@worker_cc <text>` quickly; check inbox | All 5 rows present, ordered by created_at; no dedup loss (router.seen_msg_ids is per-message_id, not per-content). |

## Container-deploy preconditions (R161)

Container's lark-cli can't reach the macOS keychain, so `bot` identity
fails with `[10003] invalid param` unless these env vars are set on the
container (typically via gitignored `.env` + docker-compose):

- `FEISHU_APP_ID` (or `LARKSUITE_CLI_APP_ID`)
- `FEISHU_APP_SECRET` (or `LARKSUITE_CLI_APP_SECRET`)

R161's `feishu/lark.subprocess_env` auto-fetches the tenant_access_token
from the app-id/secret pair on first call (token cached in
`/tmp/claudeteam_tenant_token.json` for ~77min, refreshed automatically
60s before expiry). No manual `lark-cli auth login` needed inside the
container.

If `FEISHU_APP_ID`+`FEISHU_APP_SECRET` are absent and no keychain is
reachable (Linux container without env), `subprocess_env` returns the
plain env and lark-cli will surface its own auth error — caller sees
`no access token available for bot`.

## Evidence per command

For each tested command record:

- Trigger: real user event vs in-process fake (`subscribe.process_lines`)
- lark message_id of the bot's reply card
- Latency from user message to bot reply
- Header color + body element count (sanity)
- Any lark-cli error code if the call returned non-200
- For `/tmux` specifically: paste 3 lines of the rendered body so the
  next reviewer can see whether v2 markdown rendered them as a code
  block or fell back to literal triple-backtick text
