<p align="center">
  <b>English</b> · <a href="DEPLOYMENT_zh.md">简体中文</a> · <a href="DEPLOYMENT_docker.md">Docker →</a>
</p>

# Deployment Guide (Host)

Get a ClaudeTeam crew running — **just follow the 5 steps below, top to bottom**.
Config, model-backend, and troubleshooting reference live further down. Deploying
on Docker / a server → see [Docker deploy](DEPLOYMENT_docker.md).

> **Driving this with a coding agent?** Don't let it free-run — the botched
> installs we see are all an agent *guessing* the roster instead of asking, then
> calling `health` green without ever looking inside the panes. Before it starts,
> point it at the **[coding-agent deploy protocol](#deploying-with-a-coding-agent)**
> further down: **ask the operator the intake questions first**, then **verify
> every agent** (per its runner) before telling them the team is up.

---

## Before you begin

Install these (the bits `pip` can't):

- **Python 3.9+** — macOS's built-in `/usr/bin/python3` (3.9) is fine, nothing
  extra to install. Debian/Ubuntu also needs `sudo apt install -y python3-venv`.
- **tmux** — one window per agent.
- **node + npx (18+)** — runs `lark-cli` (sending) + the Feishu sidecar (bot
  registration + event ingress).
- **≥ 1 agent CLI** — `claude` alone is enough (the default team uses only it);
  mixing in `codex` / `gemini` / `qwen` / … is **optional** (see the
  [adapter table](../README.md#multi-cli-adapter)).
- A **Feishu / Lark account** — `--quick` scan-registers anywhere (you @ the bot in
  groups); for "un-@'d in groups", drop `--quick` and let the browser automation build an
  enterprise self-built app (needs a desktop browser).

> 💡 Agents **reuse your existing local login**: if `claude` is logged in on this
> machine, the claude agents use it directly — **no separate login**. Same for any
> other CLI — logged in locally is enough.

---

## Step 1 · Install

```bash
# Code + the claudeteam command (-e = editable install: always tracks your
# checkout, never stuck on a stale version)
git clone https://github.com/zylMozart/ClaudeTeam.git && cd ClaudeTeam
python3 -m venv .venv && source .venv/bin/activate    # macOS's built-in 3.9 is fine
pip install -e .

# External tools pip can't install:
#   macOS:  brew install tmux node && npm i -g @anthropic-ai/claude-code @zed-industries/claude-code-acp
#   Debian: sudo apt install -y tmux nodejs npm && npm i -g @anthropic-ai/claude-code @zed-industries/claude-code-acp
```

> Install only the agent CLIs you'll use. The default team is all `claude-code`,
> so `claude` + its ACP adapter (`claude-code-acp`, above) runs it. Adding
> `codex`? Also add `@zed-industries/codex-acp`. Other CLIs (kimi / gemini /
> qwen / …) need no extra adapter — they run on the tmux pane runner.
> `lark-cli` (`@larksuite/cli`) is OPTIONAL: the bot's inbound + outbound
> both ride the bundled sidecar; you only need lark-cli for `--as user`
> sends (e.g. the operator-run scenarios in tests/scenarios/).

> The `.venv` above is just *one* way. conda / pipx / system-python all work too
> — the only requirement is that `claudeteam` **and** your agent CLIs resolve on
> `PATH` in the shell you run `up` from. So the PATH fixes below say "the env
> `claudeteam` is installed in," not literally `.venv`.

## Step 2 · Configure your team

> ▶ **Agent:** resolve the roster with the operator first (Rule 1, questions
> 1–2). Build `[team.agents.*]` from **only** the CLIs that are installed *and*
> logged in — don't leave the default `worker_codex`-style examples in unless
> `codex` passed both checks.

```bash
claudeteam init --no-connect      # writes claudeteam.toml (default: manager + 1 claude worker)
$EDITOR claudeteam.toml           # adjust agents to the CLIs you have / are logged into (below)
```

Open `claudeteam.toml`; `[team.agents.*]` is your roster. The default is two
`claude-code` agents — **install claude and it just works**. To add a worker on
another CLI (only if you've **installed + logged into** it), uncomment the example
init wrote and edit it:

```toml
[team.agents.worker_codex]
cli   = "codex-cli"     # add only if you have codex installed; otherwise leave it out
model = "gpt-5.5"
role  = "Codex worker"
```

> Don't want to configure from scratch? [`templates/`](../templates/) has ready
> domain teams (software-dev / research / marketing / data / content) — copy one
> and tweak. `claudeteam reidentify <agent> --print` previews an agent's rendered
> identity before `up`.

## Step 3 · Connect Feishu (scan once → bot + group built)

> ▶ **Agent:** first check whether they already have `state/feishu_app.json` + a
> `chat_id` — if so, skip this step. Otherwise **this is where you pick the mode
> with the operator** (walk them through the `--quick` vs no-flag trade-offs
> below) — don't silently default. The QR scan itself is the operator's to do.

```bash
claudeteam feishu connect --quick     # one scan, runs anywhere (you @ the bot in groups)
```

**`--quick` is the easy path**: scan one QR, zero console, **runs on any machine** (incl.
headless servers). It creates the bot app + team group (invites you) + creds + `chat_id`
(written back to `claudeteam.toml`). The one catch: a **PersonalAgent** app can't get
`im:message.group_msg`, so **in groups you @ the bot** to get a reply — DMs are unaffected,
and it's fine to start here.

**Want the bot to reply in groups _without_ an @?** Drop `--quick`:

```bash
claudeteam feishu connect             # browser-builds an enterprise app that needs no @
```

With no flag it opens a **real (headed) browser** and drives the Feishu console to create +
scope + subscribe + **publish** an enterprise self-built app holding `im:message.group_msg`
— then the bot **replies to un-@'d group messages**. Scan the login QR **once**; the 7
console stages auto-run; on any console-UI change it **falls back to `--manual`**. Needs a
**desktop browser** (see the headless note below). There's also **`--manual`** — the
step-by-step guided console flow (paste App ID/Secret, click the one-click permission
deep-link, publish) — the robust fallback when the browser automation can't run.

> ⚠️ **Headless servers:** the no-flag browser automation needs a desktop browser, so it
> **can't run on a headless host** — either use `--quick` (you @ the bot in groups), or run
> `claudeteam feishu connect` on a **desktop machine** to build the app + group, then copy
> the saved creds (`state/feishu_app.json`) + `chat_id` into the server's config. (Headless +
> terminal-QR is planned.)

<details>
<summary><b><code>--manual</code> guided console flow</b> (if the browser automation can't run or you'd rather click it yourself)</summary>

`claudeteam feishu connect --manual` walks you through the console:

1. **Create the app** — open <https://open.feishu.cn/app> → 创建企业自建应用 → add
   the **机器人 (bot)** capability → copy the **App ID + App Secret**, paste when prompted.
2. **One-click scopes** — click the deep-link it prints (all 7 scopes incl. the
   sensitive `im:message.group_msg` pre-selected) → 确认.
3. **Event** — 事件与回调 → 订阅方式 = **使用长连接** → add the **接收消息** event.
4. **Publish** — 应用发布 → 创建版本 → 申请发布 → **批准** (tenant admins approve their own version instantly; personal-edition apps skip review).
5. Press **Enter** — the command verifies the scope, creates the group, saves creds → `state/feishu_app.json` (0600) + writes `chat_id`.

</details>

> **One command for Steps 2+3** (default team only): `claudeteam init --quick`
> writes the default config *and* scan-connects Feishu in one go; plain
> `claudeteam init` does the same but with the no-@ browser flow. Quick
> reference: `init --no-connect` = config only, connect later · `init --quick` =
> config + scan-connect · `init` = config + browser (no-@) connect.
>
> ▶ **Agent:** the one-shot `init --quick` / `init` forms **bake in the default
> roster _and_ the Feishu mode in a single command** — only reach for them
> *after* the operator has confirmed the **roster** (Rule 1) *and* picked the
> **Feishu mode** (Step 3). If either
> is still open, use the explicit Step 2 (`init --no-connect`) + Step 3 so each
> decision stays a real choice, not a default you smuggled past them.

## Step 4 · Launch

```bash
claudeteam install-hooks      # install slash-command hooks (MUST run before up)
claudeteam up                 # start the tmux crew + router + watchdog
claudeteam health             # infra self-check: binaries / env / tmux / router / watchdog
```

`up` starts a tmux session (one window per agent) + the router + the watchdog,
then kicks the manager to run a group roll-call. `health` should be green (one
`lark_profile blank` ⚠️ is tolerable). **But green `health` only means the
infrastructure is up — go to Step 5 before you trust the team.**

## Step 5 · Verify each agent

Two runners, two verification surfaces — check which one each agent is on
first (`health` prints `acp ready (…)` vs `pane ready (…)` per agent):

**ACP agents** (claude-code / codex-cli by default): the CLI runs headless
inside the router, so there is no REPL pane to eyeball — the pane is a
read-only transcript viewer. Verify by making it *do* something:

```bash
claudeteam health             # each acp agent: "acp ready" / "acp turn in flight" = good;
                              # "pending work but no live host subprocess" = router down → fix that first
# then send one real message through Feishu (@agent 报到) and watch:
claudeteam peek <agent> 40    # transcript shows "===== turn … =====" + streamed output
cat state/acp/<agent>/queue.json   # the row goes pending → prompting → done (stop_reason=end_turn)
```

A row stuck `pending` forever → router isn't running. A turn that settles
`failed` → read the error in the row + `state/facts/logs.jsonl` (auth issues
surface here, e.g. an expired login).

**Tmux agents** (kimi / gemini / qwen / … or `runner = "tmux"` pins): green
`health` ≠ a working team — it never looks *inside* the pane. Each CLI still
has to have actually launched, authenticated, and swallowed its identity
prompt — so **look at every pane**. For **each** tmux agent in your roster:

```bash
claudeteam team               # one line per agent that reported — but a DEAD pane won't appear here,
                              # so don't stop at this; peek EVERY rostered agent (dead ones are
                              # exactly the ones you need to see):
for a in $(grep -oE '^\[team\.agents\.[^]]+\]' claudeteam.toml | sed -E 's/.*agents\.([^]]+)\]/\1/'); do
  echo "===== $a ====="; claudeteam peek "$a" 40
done
```

Read each pane and match it against this table — **fix a ❌ before moving on**:

| What the pane shows | Verdict | Fix |
| --- | --- | --- |
| CLI REPL is up, the injected identity / roll-call was answered, no error banner | ✅ healthy | — |
| `claude: not found` / `codex: not found` | ❌ PATH | you `up`'d from a shell where `claude`/`claudeteam` don't resolve → re-`up` from the shell/env `claudeteam` is installed in (`.venv`, conda, pipx, …), then `claudeteam down && up` |
| "Not logged in" / a login or auth prompt | ❌ not authed | log that CLI in on this machine (`claude`, `codex login`, …); for claude, `down && up` re-materialises creds — see [Not logged in](#not-logged-in-in-a-claude-pane-macos-host) |
| "update available" / a version prompt (codex especially) | ❌ blocked | `tmux send-keys -t <session>:<agent> 3 Enter` (Skip until next version) → `claudeteam reidentify <agent>` |
| bare shell prompt or empty buffer, no CLI banner | ❌ didn't spawn | `peek` again after ~30 s; still bare → that CLI isn't really installed / on PATH |
| `invalid api key` / a base_url error (BYOK CLIs) | ❌ backend misconfig | check `OPENAI_BASE_URL` / `OPENAI_API_KEY` in the secrets `.env` — see [Model backend](#model-backend-per-agent-credentials--endpoint) |

**Then the live signal — the Feishu group.** On a fresh `up` the manager **posts
a roll-call** and each worker reports in; then `@manager 你好` → reply in ~30 s.
Optional in-group probes: `/team` (each agent's ♥ heartbeat < 30 s), `/health`
(per-agent + router + watchdog card).

Only once **every agent is verified** (acp: a real turn completed `done`;
tmux: pane ✅) *and* the group roll-call landed is the team really up — that,
not green `health`, is what you tell the operator.

**Tear down:** `claudeteam down` (stop, keep state) · `claudeteam reset` (also wipe state).

---

## Deploying with a coding agent

A coding agent (Claude Code / Codex / …) driving this deploy should follow
**three rules, in order** — they wrap the 5 steps above with the ask-first +
verify-every-agent discipline that keeps an unattended install from quietly going
wrong.

### Rule 1 · Ask before you act — the intake

Before running *anything*, ask the operator this short intake, then say the plan
back and get a nod. Never guess a default; a wrong guess here is exactly what
produces a dead team later.

1. **Which agent CLIs are installed _and logged in_ on this machine?**
   Check *install* yourself — don't ask what you can test:
   ```bash
   for c in claude codex gemini qwen kimi; do printf '%s: ' "$c"; command -v "$c" || echo "(not installed)"; done
   # ACP adapters — required for the DEFAULT runner of claude-code / codex-cli:
   for c in claude-code-acp codex-acp; do printf '%s: ' "$c"; command -v "$c" || echo "(not installed — npm i -g @zed-industries/$c, or pin runner=\"tmux\")"; done
   ```
   For each that resolves, ask the operator to confirm it's **logged in** (a CLI
   that isn't logged in comes up as a *dead pane* — this is the #1 cause of a
   silent team). **The roster only contains CLIs that pass both checks.** Don't
   put a `codex` worker in the team if `codex` isn't installed + logged in. You
   *can't* fully verify "logged in" from outside the CLI, so this stays a real
   question worth asking — it prunes the roster **before** you build panes you'd
   have to tear down. And whatever the operator answers,
   [Step 5](#step-5--verify-each-agent) is the **final gate for every agent
   — the `claude` manager included** (a not-logged-in CLI is a dead pane there):
   treat each one as **unconfirmed until its pane is ✅**. When unsure, start with
   fewer agents and add more once each is proven; never skip Step 5 just because
   the operator said "they're all logged in."
2. **Roster shape** — the default 2-agent all-`claude` team, a domain template
   from [`templates/`](../templates/) (software-dev / research / marketing /
   data / content), or a custom roster? The **manager must be `claude-code`**
   (it drives the roll-call) — if the operator picks a template, open its
   `claudeteam.toml` and confirm the lead/manager agent's `cli` is `claude-code`
   before `up`. Confirm the final agent list with the operator.

(The Feishu registration mode — `--quick` vs no-flag — is its own decision; you
make it *with* the operator at **Step 3**, where the trade-offs are laid out. No
need to front-load it into the intake.)

Then **state the plan back** — e.g. *"roster = manager + worker_cc, both
claude-code, on your existing local claude login"* — and only proceed on a yes.

### Rule 2 · Follow the steps; read code only as a last resort

Steps 1–5 are the *whole* procedure — run them as written rather than
reverse-engineering your own path from the source. When something fails, work it
in this order: (1) the [Common failures](#common-failures) table, (2) the
failing command's own output / logs, (3) escalate to the operator with that
output. **Reading the source is the *last* resort, not the first** — it isn't
off-limits, but reach for it only *after* you've actually run the steps and hit
a problem none of the above resolves; then read to diagnose that specific
failure, and still surface it to the operator rather than silently editing code.

### Rule 3 · Verify every agent before you declare success

`claudeteam health` going green proves the **infrastructure** is up — router,
watchdog, tmux, adapter binaries — **not** that each agent can actually run a
turn. After `up`, walk [Step 5](#step-5--verify-each-agent): for ACP agents
push one real message through and watch its queue row settle `done`; for tmux
agents eyeball every pane. Only tell the operator "the team is up" once every
agent passed its runner's check **and** the manager's group roll-call landed.

---

## Configuration: `claudeteam.toml`

Single TOML file (Cargo-style, comment-friendly) — `claudeteam init` writes it,
documented in-place. App creds are **not** here (they live in
`state/feishu_app.json`); only `chat_id` + the team layout.

```toml
chat_id      = "oc_..."                       # Feishu group chat_id (written by `feishu connect`)
lark_profile = ""                             # lark-cli profile name; "" = default
default_model = "opus"                        # fallback when an agent doesn't pin one

[team]
session = "ClaudeTeam"                        # tmux session name

[team.agents.manager]
cli = "claude-code"                           # claude-code | codex-cli | gemini-cli | kimi-code | qwen-code
                                              #   | minimax | opencode | codewhale | openclaw | trae | hermes | pi
role = "团队主管"                             # rendered into identity.md
model = "opus"
runner = "acp"                                # optional — acp | tmux. Default: acp when the CLI has an
                                              #   ACP adapter (claude-code / codex-cli), else tmux.
                                              #   Pin "tmux" to force the legacy pane path.
specialty  = ["调度", "审阅"]                 # optional — manager sees this in dispatch prompt
tone       = "稳重克制"                       # optional — biases LLM tone
notes      = "always answer in Chinese"       # optional — free-form prompt addendum
playbook   = "manager.md"                     # optional — a role-instruction .md (→ its CLAUDE.md/AGENTS.md)
card_color = "blue"
publish_overrides = { worker_to_user = false } # per-agent override of [chat.publish]

[standup]                                     # periodic progress reports (see the Standup section)
enabled = true
interval_minutes = 10                         # cadence while work is in flight; idle team = silence
activity_window_minutes = 45
target = "manager"                            # who inspects everyone and reports to the boss

[chat.publish]                                # who-talks-to-whom group filter
user_to_manager   = "always"                  # boss → manager (always lands)
manager_to_user   = "always"                  # manager → boss (always lands)
manager_to_worker = true                      # show dispatch cards in group
worker_to_manager = true                      # show worker progress in group
worker_to_user    = true                      # show worker completions in group
worker_to_worker  = true                      # show inter-worker pings in group
```

Defaults are wide open (everything visible) — flip individual keys to `false`
once the team's noise level needs trimming. **Override precedence** (highest
wins): `env` > `claudeteam.toml` > code default (see `runtime/tunables.py`).

**Team templates** — instead of hand-writing the roster, start from a domain
template in [`templates/`](../templates/) (software-dev, automated-research,
marketing-growth, data-analysis, content-ops): a ready `claudeteam.toml` plus a
per-role **playbook** `.md` per agent. An agent's `playbook` file becomes the bulk
of its identity — its native `CLAUDE.md` / `AGENTS.md` — layered on top of the team
protocol, so each shows up knowing its job, not just a one-line title. Copy a
folder's contents next to your `claudeteam.toml` (the `playbook` paths resolve
relative to it) and adapt. Write your own for any domain — it's just a `.md`.
Preview what an agent will get with `claudeteam reidentify <agent> --print` — it
renders that agent's identity (role + playbook + team protocol) to stdout, no live
team needed, so you can check a config or playbook edit before `up`.

---

## Agent CLIs

Each agent runs a coding CLI — install the ones you'll use (ClaudeTeam just needs it on PATH).
The default team is all `claude-code`, so `claude` alone runs it.

| Adapter | `cli` | Install |
| ------- | ----- | ------- |
| Claude Code | `claude-code` | `npm i -g @anthropic-ai/claude-code` |
| Codex CLI | `codex-cli` | `npm i -g @openai/codex` |
| Kimi Code | `kimi-code` | `uv tool install kimi-cli` |
| Gemini CLI | `gemini-cli` | `npm i -g @google/gemini-cli` |
| Qwen Code | `qwen-code` | `npm i -g qwen-code` |
| MiniMax Mini-Agent | `minimax` | `uv tool install "git+https://github.com/MiniMax-AI/Mini-Agent.git"` |
| opencode | `opencode` | `npm i -g opencode-ai` |
| CodeWhale | `codewhale` | `npm i -g codewhale` |
| OpenClaw | `openclaw` | `npm i -g openclaw` · needs Node ≥ 22 |
| Trae | `trae` | `uv tool install --with docker --with pexpect "git+https://github.com/bytedance/trae-agent.git"` |
| Hermes | `hermes` | `curl -fsSL https://hermes-agent.nousresearch.com/install.sh \| bash -s -- --skip-setup` |
| Pi | `pi` | `npm i -g @mariozechner/pi-coding-agent` |

### ACP runner (claude-code / codex-cli)

Agents on ACP-capable CLIs default to `runner = "acp"`: the CLI runs
headless as a router subprocess speaking the
[Agent Client Protocol](https://agentclientprotocol.com/) (JSON-RPC over
stdio), which gives durable queued delivery with ACKs, exact busy/idle
state, and a deterministic `/stop` — instead of tmux send-keys + screen
scraping. The agent's tmux window becomes a read-only transcript viewer.

Install the adapters next to the CLIs:

```bash
npm i -g @zed-industries/claude-code-acp @zed-industries/codex-acp
```

Pin `runner = "tmux"` on an agent in `claudeteam.toml` to keep the legacy
pane behavior (CLIs without ACP support — kimi, gemini, qwen, … — use it
automatically). Ops surface for ACP agents: `state/acp/<agent>/queue.json`
(delivery states), `transcript.log` (what the viewer shows),
`claudeteam peek <agent>` reads the transcript directly.

### Standup: periodic progress reports

While the team is actively working, the router nudges the manager every
`interval_minutes` to inspect every agent and post one consolidated
progress report (who's doing what, overall progress, blockers, next
steps) to the group. Idle team → no reports. `/standup` in chat triggers
one immediately.

```toml
[standup]
enabled = true              # default true
interval_minutes = 10       # report cadence while work is in flight
activity_window_minutes = 45
target = "manager"          # who inspects and reports
```

The last seven are **OpenAI-compatible** (BYOK) — credentials + endpoint below.

---

## Model backend per agent (credentials + endpoint)

**A first boot needs none of this** — the 2 default agents run on your Claude
Code OAuth (reusing your local login). Come here only when you swap an agent onto
a non-Anthropic backend.

The adapters are **provider-agnostic** — nothing about DeepSeek/OpenAI/etc. is
baked in. You choose the backend through env + config:

- **Credential** — resolved by `runtime/agent_auth`, priority **token > login >
  api_key** (higher present overrides lower). Secrets live in a gitignored env
  file (`$CLAUDETEAM_SECRETS_FILE`, default `<state_dir>/.env`) or the process
  env — never in `claudeteam.toml`. Per-agent override: `<AGENT>_<VAR>` (e.g.
  `WORKER_PI_OPENAI_API_KEY`).
  - **claude-code / codex / kimi** — their own token/login/api_key vars.
  - **all other CLIs** (minimax, opencode, codewhale, openclaw, trae, hermes, pi)
    — the **api_key** tier: set `OPENAI_API_KEY`.
- **Endpoint** — `OPENAI_BASE_URL` (e.g. `https://api.openai.com/v1` or a
  self-hosted vLLM/Ollama URL — any OpenAI-compatible API). **Model** — the
  `model` field in each `[team.agents.<name>]`.
- **Provider name** (only where a CLI needs one selecting an OpenAI-compatible
  *chat/completions* client): `CLAUDETEAM_TRAE_PROVIDER` (default `openrouter`),
  `CLAUDETEAM_PI_PROVIDER` / `CLAUDETEAM_CODEWHALE_PROVIDER` (default `openai`).
- A **claude-code manager on a non-Anthropic backend** uses the
  Anthropic-compatible vars: `ANTHROPIC_BASE_URL` + `ANTHROPIC_AUTH_TOKEN`
  (+ `ANTHROPIC_MODEL` / `ANTHROPIC_DEFAULT_*_MODEL`).

Example — point the OpenAI-compatible workers (and, optionally, a claude-code
manager on a non-Anthropic backend) at **any** provider, via `docker -e` or the
host shell. Swap the host for whatever you use (a hosted API or a local server):

```bash
OPENAI_BASE_URL=https://your-provider.example/v1   # the provider's base URL
OPENAI_API_KEY=sk-...                              # your key for it
# a claude-code manager on a non-Anthropic backend uses the Anthropic-compatible vars:
ANTHROPIC_BASE_URL=https://your-provider.example/anthropic
ANTHROPIC_AUTH_TOKEN=sk-...
```

The per-CLI `CLAUDETEAM_<CLI>_PROVIDER` vars (see above) pick the
chat/completions client — leave them at their defaults unless your provider needs
a specific one. See each CLI's `tests/scenarios/<cli>.md` for concrete, per-provider specifics.

---

## Agents talking to each other: `send` vs `say`

| Command | What it does | Reaches the worker's pane? |
| --- | --- | --- |
| `claudeteam send <to> <from> <msg>` | Inbox row **+** tmux pane inject | **Yes** — wakes the recipient directly |
| `claudeteam say <agent> "<msg>" --to <role>` | Post into Feishu chat (subject to `[chat.publish]`) | Only if the router relays it back |
| Feishu group → router → `deliver.apply` | Inbound chat → inbox row + pane inject | **Yes** — wakes a worker on boss/manager input |

**Always pass `--to`** on `say`: `--to user` = answering the boss; `--to manager`
= internal progress; `--to worker_<name>` = peer ping. Omitting it falls back to
`user` and defeats the publish filter.

---

## Multi-team isolation

State lives in a `state/` dir **beside each team's `claudeteam.toml`** — the
config's location *is* the team's identity. Running a second team needs no
special env; just keep each team in its own directory:

```bash
cd /path/to/team-a && claudeteam up
# different shell:
cd /path/to/team-b && claudeteam up
```

Each team keeps its own `team-a/state/` and `team-b/state/`, so their agents,
status, and inboxes never bleed together. Override the location with
`CLAUDETEAM_STATE_DIR` if you want state elsewhere (e.g. a Docker volume).

Each team still needs its **own Feishu app** (independent app_id/secret) —
sharing one across teams causes credential leakage + event-routing conflicts.

---

## Commands

**Operator CLI** — `claudeteam --help` lists everything grouped by section (it's
self-maintaining; trust it over any table here). The everyday ones: `up` / `down`
/ `health` / `team` / `peek <agent>` / `usage` / `reidentify` / `remember` /
`recall` / `switch`.

**Chat-side slash commands** (after `install-hooks`, recognised in the manager
pane; the boss can also send them — they zero-LLM dispatch through the router):

| Slash | What it does |
| --- | --- |
| `/help` | List all slash commands (card) |
| `/team` | All agents' live pane state |
| `/health` | Server CPU / memory / disk card |
| `/usage` | Token/credit usage (ccusage / codex / kimi) |
| `/tmux [agent] [N]` | Capture last N lines of a pane |
| `/send <agent> <msg>` | Inject a message into a pane |
| `/compact [agent]` | Compact the CLI's context + scheduled re-identify |
| `/stop [agent]` | Interrupt the agent (acp: session/cancel; tmux: Esc — agent stays alive) |
| `/clear <agent>` | `/clear` the CLI + re-inject identity |
| `/task [all]` | Read-only task kanban |
| `/shutdown [confirm]` | Panes offline, keep router/watchdog for `/restart` |
| `/restart` | Restart the whole team (≈ down→up) |
| `/login <cli> [agent]` | Trigger a CLI re-auth; surfaces the verification URL/code |

---

## Common failures

### `claudeteam feishu connect` hangs / says "cancelled"

A non-interactive terminal (piped / non-TTY) or a Ctrl-C gives "cancelled (no
input / non-interactive terminal)" — re-run it in an **interactive** terminal.
The no-flag (browser-automated) mode needs a **desktop browser**, so it can't run
on a headless server — there, run it on a desktop machine and copy
`state/feishu_app.json` + `chat_id` over (or use `--quick` / `--manual`, which need
no browser). If the console UI changed under it, it falls back to `--manual`; you
can also force `--manual` to click through it yourself. `--quick` prints its QR
before waiting for your scan.

### Group messages get no response after `up` / router keeps restarting

Usually the **sidecar's WebSocket (long connection) never came up** — the router
spawns the sidecar to receive events; if it can't connect it errors out and exits,
the router exits with it, the watchdog respawns it, and round it goes. The router
log shows `⚠️ subscribe child exited` followed by a **`↳ sidecar 最后输出` + `↳ 诊断`**
block; the two fixes it points to:
1. **The app has no long-connection subscription** → Feishu console → Events &
   callbacks → subscription mode → switch to "Receive events via long connection"
   (NOT Webhook URL), save. (`--quick` usually sets this up; check it on a
   hand-built app.)
2. **An HTTPS_PROXY is blocking the WebSocket** → `export LARK_CLI_NO_PROXY=1`
   before launch, or set it in `$CLAUDETEAM_SECRETS_FILE` (default `<state>/.env`)
   / your shell profile.

Ingress works the moment the sidecar connects; if it IS connected but the group is
still silent, check the manager's `claude` login (entries below).

### A tmux agent's message sits in its input box, never submitted

`tmux attach`, select the agent's window: if routed text is visible inside the
CLI's composer (e.g. kimi's `── input ──` frame) and nothing happens for
minutes, the adapter's submit key doesn't match your CLI version's TUI. Quick
confirm: press Enter in that pane yourself — if the text submits, that's it.
Fix: update that adapter's `submit_keys()` (see `agents/CLAUDE.md`), or move
the agent to the ACP runner if its CLI has an adapter (no keys involved).
Watch out for this after any CLI version upgrade — TUI keybindings drift.

### `claude: not found` / `codex: not found` in a pane

Panes inherit the launching shell's `$PATH`. If you opened a fresh terminal and
forgot to activate the env `claudeteam` is installed in (a `.venv`, or your
conda / pipx / system-python env), the pane can't resolve the CLIs. Re-`up` from
a shell where **both** `command -v claudeteam` and `command -v claude` (and any
other agent CLI in your roster) resolve.

### "Not logged in" in a claude pane (macOS host)

Each pane has its own `~/.claude/.credentials.json` snapshot (seeded from your
local login, per-agent home isolation), which can go stale vs the keychain. Fix:
`claudeteam down && up` re-materialises it.

### `router.log` shows "no live events … rotating subscribe" every ~120 s

**Usually NORMAL, not a fault — especially on macOS.** On an idle chat the
WebSocket goes quiet; the router self-SIGTERMs (`_watch_subscribe_health`),
watchdog respawns it, and catchup refetches anything missed from Feishu's REST
API — the recovery loop *is* the design. The platform-aware idle threshold is
Darwin 120 s / Linux 600 s (override `router.stale_event_threshold_s` in the toml
or `CLAUDETEAM_ROUTER_STALE_S`). Two shapes:

- `ℹ️ no live events for Ns — rotating subscribe (none inbound yet …)` — idle, expected.
- `⚠️ live events stopped after Ns idle …` — events WERE flowing and stopped (notable, esp. on Linux).

The log never prints "I received your message" — trust `claudeteam health`'s
`inbound:` line + one real group message instead. If the `⚠️` is *constant*,
look for a second sidecar stealing events:
`ps -ef | grep -E "feishu_channel/sidecar\.js run" | grep -v grep`.

### Manager loops on the same anchored message after `up`

Catchup replays everything newer than the cursor (with a `state/router.seen`
dedup set, auto-trimmed at 5000). Still duplicating? Delete `state/router.seen`
and bump `state/router.cursor` forward to "now" so the next catchup skips older.

### `say` from a pane fails HTTP 400 "Bot/User can NOT be out of the chat"

`say` from your launching shell works, but the same call from inside a pane
fails. Cause: a pre-existing tmux **server** (from an earlier `up`, different
checkout) holds its original global env, and `tmux new-session` inherits *that*,
not your shell's. The lifecycle prefix now embeds the creds per spawn-cmd, so a
clean state shouldn't trigger it. If it still does:

```bash
tmux ls 2>/dev/null
ps -ef | grep -E "claudeteam (router|watchdog)|feishu_channel/sidecar\.js" | grep -v grep
claudeteam down
tmux kill-session -t ClaudeTeam        # or `tmux kill-server` if no other tmux work
claudeteam up
```

### `say` / sidecar can't find App credentials

Outbound cards fail, or the sidecar exits complaining it has no app id/secret.
Creds resolve from one source: `state/feishu_app.json` (written by
`feishu connect`, 0600), which `feishu/lark.py:subprocess_env()` reads to inject
`FEISHU_APP_ID`/`SECRET` + a tenant token into both the sidecar (ingress) and
lark-cli (egress). `ls -l state/feishu_app.json` (expect `-rw-------`); if
missing, re-run `claudeteam feishu connect`.

### `worker_codex` (or any codex agent) shows "pane up but CLI not ready yet"

Codex sometimes opens with an "update available" prompt blocking the ready marker:

```bash
tmux send-keys -t ClaudeTeam:worker_codex 3 Enter   # "Skip until next version"
claudeteam reidentify worker_codex
```

---

## Where things live

```
src/claudeteam/
├── cli.py             single console-scripts entry; dispatch only
├── commands/          one module per subcommand (~30-300 LOC each)
├── store/             local file-backed state (inbox, status, logs, tasks, memory)
├── agents/            CliAdapter base + per-CLI adapters + identity renderer
├── runtime/           config / paths / tmux / watchdog / pidlock / wake / lifecycle / tunables
└── feishu/            lark-cli wrapper + chat + router + slash + deliver + subscribe + catchup
scripts/feishu_channel/  the @larksuite/channel sidecar (registration + ingress)
tests/                 unit/ + integration/ (stdlib runner) + scenarios/ (operator playbooks)
```

`CLAUDE.md` (project root) holds the building rules + active work order — read it
before changing code.

---

## Stuck? Found a bug?

Under active development — we **respond within 12 hours**.

- 🐛 **GitHub issue** — [open one](https://github.com/zylMozart/ClaudeTeam/issues/new/choose).
  Include OS, deploy mode (host vs Docker), and the failing command's output (for
  `feishu connect` issues, the sidecar's stderr).
- 💬 **WeChat community group** — scan the QR in the [README](../README.md#need-help--found-a-bug).

If you're an AI agent driving a deploy and a step fails after a real recovery
attempt, surface this section to the user — there's a real maintainer reachable.
