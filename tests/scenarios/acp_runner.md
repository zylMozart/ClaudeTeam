# Scenario: ACP runner end-to-end (operator-run)

Verifies the ACP delivery path against a REAL deployment: queue-backed
delivery with ACKs, precise busy/idle state, deterministic /stop, crash
recovery, and the tmux viewer pane. Complements the in-process gate
(`python3 tests/run.py acp`) which covers the same machinery against the
fake agent.

## Prereqs

- Deployed team per `docs/DEPLOYMENT.md`, with at least one ACP-capable
  agent (cli `claude-code` or `codex-cli`, no `runner = "tmux"` pin).
- ACP adapters on PATH: `which claude-code-acp codex-acp`
  (`npm i -g @zed-industries/claude-code-acp @zed-industries/codex-acp`).
- `claudeteam up` green; Feishu group connected.

## 1. Delivery ACK trail

**Given** the team is up and idle
**When** you send a group message routed to an ACP agent (e.g.
`@worker_cc 用一句话报到`)
**Then**
- `state/acp/<agent>/queue.json` gains a row that transitions
  `pending → prompting → done` with `stop_reason: "end_turn"`;
- `claudeteam log <agent>` shows an `acp_turn` entry whose `ref` is the
  row's qid;
- the agent's reply lands in the Feishu group (it still replies via
  `claudeteam say` from its Bash tool — the reply channel is unchanged).

## 2. Viewer pane + peek

**Given** scenario 1 just ran
**When** you `tmux attach -t <session>` and select the agent's window
**Then** the pane is a read-only tail of the turn (`===== turn … =====`
separators, streamed text) — and `claudeteam peek <agent> 50` prints the
same transcript tail without tmux.

## 3. Precise busy/idle in /team

**Given** you send an ACP agent a long task (e.g. "sleep 30 然后报告")
**When** you post `/team` in the group while it runs, and again after
**Then** the card shows `🔄 working · acp` during the turn and
`💤 idle · acp` after — no pane scraping involved (kill the viewer pane
and the state stays correct).

## 4. Deterministic /stop

**Given** an ACP agent is mid-turn on a long task
**When** you post `/stop <agent>`
**Then** the turn ends promptly; the queue row settles with
`stop_reason: "cancelled"`; the agent stays alive for the next message.

## 5. Message survives a router kill (the T1 guarantee)

**Given** the team is up
**When** you `kill -9 $(cat state/router.pid)`, then IMMEDIATELY send
`@worker_cc 复述暗号 X-7`, and wait for the watchdog respawn (≤60s)
**Then** after the router returns, the agent receives and answers the
message exactly once — the queue row (written by catchup delivery) is
consumed by the fresh AcpHost; nothing is lost, nothing double-runs.

## 6. Adapter crash recovery

**Given** an ACP agent is idle
**When** you `kill -9` its adapter process (`cat state/acp/<agent>/agent.pid`)
and send it a new message
**Then** the host respawns the subprocess, re-runs the identity turn on
the fresh session (visible in the transcript), and answers the message.
`claudeteam restart <agent>` likewise recycles it deliberately.

## 7. Hire while running

**Given** the team is up
**When** you add a new claude-code agent to `claudeteam.toml` and
`claudeteam hire <name>` WITHOUT restarting the router, then @ it in the group
**Then** within ~5s (roster refresh) a worker picks up its queue and it
answers — no router restart needed.

## 8. /shutdown actually silences ACP agents

**Given** the team is up
**When** you post `/shutdown 确认`, then (as a test) `claudeteam send <acp-agent> user "还在吗"`
**Then** the message queues but is NOT consumed (agent paused, no reply,
no token spend); after `/restart` the agent wakes, resumes its old session
context, and drains the held message.

## 9. Mixed team non-interference

**Given** a team with both an ACP agent and a tmux agent (e.g. kimi)
**When** you `@` each of them in one message ("@worker_cc @worker_kimi 各自报到")
**Then** both reply; `/team` renders both (acp glyph vs pane state); the
tmux agent's inject path is untouched by the ACP machinery.
