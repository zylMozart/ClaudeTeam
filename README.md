# ClaudeTeam (rebuild)

> *Harness your Claude Code.*

A clean-slate rebuild of [ClaudeTeam](https://github.com/zylMozart/ClaudeTeam):
multiple Claude Code agents in tmux, coordinated through a Feishu group
chat, with one Python module per command and a small dependency
footprint.

This branch (`rebuild/minimal`) is ~9 K LOC + tests vs. ~33 K on the
original `main`. Same UX, fewer moving parts.

> **One-click deploy — paste this into a fresh `claude` session in this
> repo:**
>
> ```
> Read CLAUDE.md and docs/DEPLOYMENT.md, then walk me through bringing
> up a ClaudeTeam deployment. Ask for the Feishu app credentials and
> chat_id when you need them.
> ```

---

### Screenshots

**Feishu group chat — control your AI team in real time**

<table><tr>
<td><img src="docs/media/example/feishu_example1.jpg" width="200" /></td>
<td><img src="docs/media/example/feishu_example2.jpg" width="200" /></td>
<td><img src="docs/media/example/feishu_example3.jpg" width="200" /></td>
<td><img src="docs/media/example/feishu_example4.jpg" width="200" /></td>
<td><img src="docs/media/example/feishu_example5.jpg" width="200" /></td>
</tr></table>

**tmux backend — Claude Code agents running in parallel**

<p><img src="docs/media/example/tmux_example.png" width="800" /></p>

---

## What it does

```
You (Feishu group chat)
  ↕  WebSocket
Router (long-poll subscribe → classify → deliver)
  ↕
┌──────────┬──────────┬──────────┐
│ manager  │ worker_X │ worker_Y │  ← tmux windows running Claude Code / Codex / Kimi / ...
│(routes)  │(executes)│(executes)│
└──────────┴──────────┴──────────┘
  ↕
Local store (inbox / status / logs / tasks / durable memory)
```

The boss talks to **manager** in the group chat. Manager dispatches work
to workers, watches their tmux panes, summarises back to the group.
Workers say-back when they finish. Everything is auditable on disk;
nothing depends on a remote DB.

---

## Features

- **One config file** — `claudeteam.toml` (Cargo-style, comment-friendly).
  Replaces the old `team.json` + `runtime_config.json` split.
- **R174 single-interface routing** — every group message goes to the
  manager only; workers never get a raw boss message. Manager is the
  sole orchestrator.
- **`[chat.publish]` filter** — sender→receiver visibility per channel.
  Silence noisy traffic without losing the audit log.
- **Multi-CLI** — `claude-code` / `codex-cli` / `kimi-code` /
  `gemini-cli` / `qwen-code` in the same team.
- **Durable memory** — `claudeteam remember` / `recall` writes survive
  `/clear` and pane respawn, auto-injected on next wake.
- **Watchdog** — daemons respawn with cooldown + Feishu chat alert when
  cooldown trips.
- **Slash commands from chat** — `/help /team /health /usage /tmux
  /send /compact /clear /stop /peek /say /remember /recall`.
- **Stdlib-only test runner** — `python3 tests/run.py` in 30 seconds.

---

## Prerequisites

| Need | Version | Why |
| ---- | ------- | --- |
| Python | 3.10+ | `pyproject.toml` pins it |
| tmux | any | one window per agent |
| Node + npx | 18+ | `lark-cli` is a node binary |
| At least one CLI | latest | `claude` / `codex` / `kimi` / `gemini` / `qwen` |
| Feishu enterprise | — | custom app with `im:message` + WebSocket subscription |

For Docker: just Docker 20.10+ and Compose v2 (CLIs come with the
container or via bind-mount).

---

## Quick start

```bash
git clone https://github.com/zylMozart/ClaudeTeam.git --branch rebuild/minimal
cd ClaudeTeam

# Shell env (per terminal — add to ~/.zshrc to persist)
export CLAUDETEAM_STATE_DIR="$PWD/state"
export LARK_CLI_NO_PROXY=1
export CLAUDETEAM_LARK_SEND_AS=bot

# Install
python3 -m venv .venv && source .venv/bin/activate
pip install -e .

# Config
claudeteam init                  # writes claudeteam.toml
$EDITOR claudeteam.toml          # set chat_id + agents
claudeteam install-hooks         # claude-code slash commands

# Launch
claudeteam up                    # tmux + agents + router + watchdog
claudeteam health                # green/yellow/red snapshot
```

Chat with the team in your Feishu group. Manager handles dispatch.

For detailed setup, Docker, multi-team isolation, and troubleshooting
see **[docs/DEPLOYMENT.md](docs/DEPLOYMENT.md)**.

---

## Multi-CLI adapter

Different agents can run different CLIs in the same team:

| Adapter | Identifier | Install |
| ------- | ---------- | ------- |
| Claude Code | `claude-code` (default) | `npm i -g @anthropic-ai/claude-code` |
| Codex CLI | `codex-cli` | `npm i -g @openai/codex` |
| Kimi Code | `kimi-code` | `uv tool install kimi-cli` |
| Gemini CLI | `gemini-cli` | `npm i -g @google/gemini-cli` |
| Qwen Code | `qwen-code` | `npm i -g qwen-code` |

In `claudeteam.toml`:

```toml
[team.agents.manager]
cli = "claude-code"
model = "opus"
role = "团队主管"

[team.agents.worker_codex]
cli = "codex-cli"
model = "gpt-5.5"
role = "数据分析员工"

[team.agents.worker_kimi]
cli = "kimi-code"
role = "策划员工"
```

---

## Documentation

| Doc | What's in it |
| --- | ------------ |
| [`docs/DEPLOYMENT.md`](docs/DEPLOYMENT.md) | Host + Docker setup, config schema, multi-team isolation, troubleshooting |
| [`CLAUDE.md`](CLAUDE.md) | Building rules + active work order — read before changing code |
| [`tests/scenarios/host_smoke.md`](tests/scenarios/host_smoke.md) | One-minute smoke for fresh deploys |
| [`tests/scenarios/round_c_real_task.md`](tests/scenarios/round_c_real_task.md) | Real-task end-to-end (manager dispatches, workers say-back) |
| [`tests/scenarios/slash_matrix.md`](tests/scenarios/slash_matrix.md) | Per-slash expected card behaviour |
| [`tests/scenarios/reidentify.md`](tests/scenarios/reidentify.md) | Identity re-injection after prompt change |

For Feishu app setup itself (creating the bot, scopes, callbacks),
see the original `main` branch's
[Feishu bot setup guide](https://github.com/zylMozart/ClaudeTeam/blob/main/docs/setup_feishu_bots.md)
or the
[Playwright auto-creator script](https://github.com/zylMozart/ClaudeTeam/tree/main/scripts/feishu_bot_creator).

---

## FAQ

**Q: How does this differ from `main`?**
A: Same UX, fewer files. `main` accumulated 33 K LOC across ~200
files; this rebuild is ~9 K with one Python module per subcommand
(`src/claudeteam/commands/<name>.py`). Single config file
(`claudeteam.toml`), no Bitable / kanban projection, stdlib test
runner, no compatibility shims.

**Q: Does it work with non-Anthropic models?**
A: Yes — the multi-CLI adapter table above shows the supported CLIs.
Each agent picks one in `claudeteam.toml`.

**Q: Can I use Slack / Discord instead of Feishu?**
A: Not out of the box. The chat layer is Feishu-specific
(`src/claudeteam/feishu/`).

**Q: How many agents can I run?**
A: Tested up to 5. Each Claude Code pane uses ~200-400 MB; 8 GB host
RAM is comfortable for 5.

**Q: An agent crashed — do I lose context?**
A: No. Inbox + status + logs + durable memory live on disk. Watchdog
respawns the daemon; `claudeteam reidentify <agent>` re-injects the
identity prompt with prior memory pre-loaded.

**Q: How much does it cost?**
A: ClaudeTeam is MIT-licensed and free. Costs come from your CLI's
API usage. Feishu free tier and `lark-cli` are free.

---

## Contributing

This is a rebuild branch — see [`CLAUDE.md`](CLAUDE.md) for the building
rules (two-use rule, single-file ceiling ~300 LOC, every new module
ships its own unit test in the same commit, etc.).

Test gate must stay green:

```bash
python3 tests/run.py     # → tests: N passed, 0 failed
```

PRs welcome. Major changes please open an issue first to discuss the
design — `rebuild/minimal` actively resists scope creep.

## License

[MIT](LICENSE)
