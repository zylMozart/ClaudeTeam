# Troubleshooting

Last updated: 2026-04-23

## How To Use This Page

1. Find your symptom below.
2. Run the listed quick checks.
3. Apply the minimum corrective action.
4. Capture evidence and report.

## Symptom Matrix

### 1) Group messages arrive but no agent responds

Quick checks:

- router process alive (`pgrep -f feishu_router.py`)
- router cursor heartbeat freshness (`scripts/.router.cursor` mtime)
- subscription profile/app mismatch

Likely causes:

- event subscription missing or stale
- router disconnected and not recovering
- wrong profile/chat boundary

First action:

- restart router path only, then verify one real message flow

### 2) Message stuck / delayed delivery

Quick checks:

- inspect `workspace/shared/.pending_msgs/*.json`
- confirm target pane is idle or wakeable
- check manager unread backlog warnings

Likely causes:

- busy pane blocked injection
- queue backlog not draining

First action:

- preserve FIFO queue; avoid manual queue file edits unless incident lead approves

### 3) `send` reports partial success or group notify failure

Quick checks:

- local inbox write happened
- Feishu group send call status

Likely causes:

- remote IM temporary failure while local core remains healthy

First action:

- do not blindly retry if local write already succeeded (avoid duplicates)

### 4) Frequent `800004135` rate-limit errors

Quick checks:

- router catch-up polling behavior
- kanban daemon write frequency
- workspace log fan-out volume

Likely causes:

- bursty Bitable writes or high-frequency polling

First action:

- reduce non-critical remote writes before touching core routing logic

### 5) Slash command returns duplicate responses

Quick checks:

- stale/duplicate router subscription processes
- old container/session still consuming same events

Likely causes:

- multiple active subscribers on same app/profile/chat

First action:

- stop stale subscriber first; keep only one valid router path

### 6) Live smoke blocked with `message_count=0`

Quick checks:

- test group/user profile readiness
- app/profile isolation correctness
- QA approved user availability

Likely causes:

- no real user-text event source in test group

First action:

- keep result red; do not claim pass from rehearsal-only bot traffic

## Escalation Rule

Escalate to manager when:

- security boundary may be violated,
- owner action or external credential operation is required,
- repeated restart attempts enter cooldown without recovery.
