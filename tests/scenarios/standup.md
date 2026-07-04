# Scenario: standup 定时巡视汇报 (operator-run)

Verifies the periodic progress-report loop: while the team is actively
working, the manager inspects everyone every `interval_minutes` and posts
one consolidated report to the group; when idle, silence.

## Prereqs

- Deployed team per `docs/DEPLOYMENT.md`, `claudeteam up` green.
- For a fast test, shrink the cadence in `claudeteam.toml` then
  `claudeteam down && claudeteam up`:

  ```toml
  [standup]
  interval_minutes = 2
  activity_window_minutes = 10
  ```

## 1. Active team → periodic report

**Given** the team is up
**When** you give the team a real multi-minute task in the group (e.g.
"@manager 安排 worker 统计仓库每个目录的代码行数，慢慢来")
**Then** within ~2 minutes (the shrunk interval) the manager posts a
progress report to the group covering: what each agent is doing, overall
progress, blockers, next steps — WITHOUT you asking. A second report
follows after the next interval while work continues.

## 2. Idle team → silence

**Given** the task finished and nobody sends new messages
**When** you wait 2× the interval past `activity_window_minutes`
**Then** no further standup reports appear in the group (check
`state/standup.json` — `last_report_at_ms` stops advancing).

## 3. /standup on demand

**Given** the team is idle
**When** you post `/standup` in the group
**Then** the manager immediately runs one 巡视 round and posts a (short,
"团队空闲") report — the slash bypasses both the interval and the
activity gate.

## 4. Router restart keeps the cadence

**Given** a report just fired
**When** you `claudeteam down && claudeteam up` within the interval
**Then** the next report does NOT double-fire immediately after boot —
`state/standup.json` persists the clock across restarts.
