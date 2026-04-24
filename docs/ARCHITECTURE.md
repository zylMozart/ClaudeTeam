# Architecture

Last updated: 2026-04-24

## One-line Positioning

ClaudeTeam is a Feishu-driven multi-agent coordination system where tmux is the runtime scheduler and local facts are the default source of truth, with Feishu/Bitable kept as explicit remote adapters.

## System Boundary

- Platform: Feishu single-platform collaboration entry.
- Runtime: tmux windows host manager and worker CLIs.
- Core facts: local file-backed state in `workspace/shared/facts` and queue files.
- Remote projection: Feishu IM/Bitable/Docs for visibility, legacy compatibility, and document publishing.

## Core Components And Responsibilities

| Component | Main file(s) | Responsibility |
|---|---|---|
| Manager control plane | `agents/manager/*`, `scripts/feishu_msg.py` | Task dispatch, progress orchestration, acceptance, owner reporting. |
| Worker agents | `agents/*/identity.md`, tmux windows | Execute assigned tasks and report back with evidence. |
| Router | `scripts/feishu_router.py` | Consume Feishu events, parse slash commands, route messages, wake agents, maintain cursor/heartbeat. |
| Queue | `scripts/msg_queue.py` | FIFO pending delivery when target pane is busy or not injectable. |
| Messaging API | `scripts/feishu_msg.py` | Stable command wrapper: `send/say/inbox/read/status/log/workspace`. |
| Local facts | `scripts/local_facts.py` | Persist inbox/status/logs as local durable files. |
| Kanban sync | `scripts/kanban_sync.py` | Optional remote kanban projection into Bitable. |
| Watchdog | `scripts/watchdog.py` | Process liveness checks, auto-restart, manager alerting. |
| Boss todo | `scripts/boss_todo.py` | Track owner-blocked tasks (default Bitable store, optional local). |
| Docs sync | `scripts/feishu_sync.py` | Sync markdown outputs to Feishu Docs/Drive. |

## C3 Command-layer Thinning (R1-ARCH-03)

`feishu_msg` now follows a thin command-layer split with legacy compatibility:

1. Script compatibility shell
- File: `scripts/feishu_msg.py`
- Keeps legacy CLI surface (`send/direct/say/inbox/read/status/log/workspace`).
- Preserves legacy `cmd_*` functions and monkeypatch points expected by compatibility tests.
- `main()` builds `handlers` and delegates argument execution to `commands.run(...)`.

2. Pure command parser/dispatcher
- File: `src/claudeteam/commands/feishu_msg.py`
- Owns `parse_argv`, `dispatch`, `run`.
- Designed as a pure command layer: argv parsing + handler routing, no direct runtime side effects.
- Maintains usage/error contract for legacy CLI behavior.

3. Service and client boundaries
- Messaging service boundary: `src/claudeteam/messaging/service.py`
  - Message sanitization, card helpers, local log/workspace command behavior.
- Feishu integration boundary: `src/claudeteam/integrations/feishu/client.py`
  - `_lark_*` wrappers and remote API call mechanics.
- `scripts/feishu_msg.py` keeps compatibility wrappers so old imports and patch-based tests remain valid.

## WATCHDOG-SVC-12 + WATCHDOG-SVC-13 + WATCHDOG-GATE-12 + WATCHDOG-GATE-13 Watchdog Layer Split

`watchdog` now follows the same thin-wrapper direction:

1. Pure supervision helper layer
- Files:
  - `src/claudeteam/supervision/watchdog_state.py`
  - `src/claudeteam/supervision/watchdog_specs.py`
  - `src/claudeteam/supervision/watchdog_health.py`
  - `src/claudeteam/supervision/watchdog_effect_plan.py`
  - `src/claudeteam/supervision/watchdog_alert_request.py`
  - `src/claudeteam/supervision/watchdog_daemon.py`
  - `src/claudeteam/supervision/watchdog_orphans.py`
  - `src/claudeteam/supervision/watchdog_messages.py`
  - `src/claudeteam/supervision/watchdog_alert_delivery.py`
  - `src/claudeteam/supervision/watchdog_proc_match.py`
  - `src/claudeteam/supervision/__init__.py`
- `watchdog_state.py` keeps deterministic state decisions (`decide_watchdog_state(...)`).
- `watchdog_specs.py` owns pure config/spec/env gating helpers:
  - `build_lark_event_subscribe_cmd`
  - `build_process_specs`
  - `env_enabled`
  - `filter_enabled_processes`
- `watchdog_health.py` owns pure health/grace/stale decision helpers:
  - `should_skip_health_file_check`
  - `is_health_file_stale`
  - `decide_health_file_state`
- `watchdog_effect_plan.py` owns pure decision->effect-plan mapping helper:
  - `build_effect_plan(...)`
  - `EFFECT_CONTINUE` / `EFFECT_ALERT_ONLY` / `EFFECT_RESTART_NOTIFY`
- `watchdog_alert_request.py` owns pure alert-request shaping helpers:
  - `normalize_alert_message`
  - `normalize_alert_log_label`
  - `build_manager_alert_send_cmd`
  - `build_testing_skip_log_line`
- `watchdog_daemon.py` owns pure watchdog pid-file/liveness helpers:
  - `parse_pid_text`
  - `is_expected_cmdline`
  - `is_live_pid_probe`
  - `pid_file_is_live`
- `watchdog_orphans.py` owns pure orphan-victim selection helpers:
  - `parse_ppid_from_status_text`
  - `select_router_tree_victims`
  - `select_orphan_victims`
- `watchdog_messages.py` owns pure alert-message builders:
  - `build_burst_alert(proc_name)`
  - `build_cooldown_alert(proc_name, max_retries, cooldown_secs)`
- `watchdog_alert_delivery.py` owns pure alert-delivery result helpers:
  - `summarize_alert_send_failure(stdout, stderr, limit=300)`
  - `build_alert_delivery_log_line(returncode, log_label, stdout, stderr)`
- `watchdog_proc_match.py` owns pure `/proc cmdline` match helper:
  - `is_lark_subscribe_cmdline(cmdline)`

2. Script compatibility shell and side-effect layer
- File: `scripts/watchdog.py`
- Pure config/process-spec/env gating now delegates to supervision helpers:
  - `_PROC_SPECS` via `build_process_specs(...)`
  - `_env_enabled(...)` via `env_enabled(...)`
  - `_enabled_procs(...)` via `filter_enabled_processes(...)`
  - `PROCS` assembled from `_enabled_procs()`
  - Router subscribe command string is composed inside `build_process_specs(...)` via `build_lark_event_subscribe_cmd(...)`
- Pure health/grace/stale decision now delegates to watchdog health helpers:
  - `should_skip_health_file_check`
  - `is_health_file_stale`
  - `decide_health_file_state`
- `check_once()` now delegates decision->effect-plan branch mapping to:
  - `_watchdog_effect_plan.build_effect_plan(...)`
- Watchdog rhythm semantics remain:
  - `healthy`
  - `healthy_reset`
  - `cooldown_wait`
  - `cooldown_ended`
  - `enter_cooldown`
  - `restart`
  - `all_ok`
- Burst/cooldown alert text now delegates to watchdog message helpers:
  - `build_burst_alert(proc_name)`
  - `build_cooldown_alert(proc_name, max_retries, cooldown_secs)`
- `_send_manager_alert` delegates pure request construction and normalization to:
  - `normalize_alert_message`
  - `normalize_alert_log_label`
  - `build_manager_alert_send_cmd`
  - `build_testing_skip_log_line`
- Watchdog self pid-lock/live decision now delegates to:
  - `_pid_file_is_live_watchdog(path)` -> `_watchdog_daemon.pid_file_is_live(...)`
- Watchdog daemon liveness semantics remain:
  - live only when pid file exists + pid probe alive + cmdline contains `watchdog.py`
  - PID reuse/unrelated cmdline -> not live
  - bad pid text -> not live
  - missing pid file -> not live
- `_kill_orphan_lark_subscribers()` now delegates pure victim selection to:
  - `_watchdog_orphans.parse_ppid_from_status_text(...)`
  - `_watchdog_orphans.select_router_tree_victims(...)`
  - `_watchdog_orphans.select_orphan_victims(...)`
- Orphan-kill semantics remain:
  - router pid live -> only kill router tree lark subscribe descendants
  - router not live or pid file missing -> only kill `PPid == 1` true orphans
  - always skip `my_pid`
- `_send_manager_alert` delegates pure result classification and failure summary to:
  - `summarize_alert_send_failure(stdout, stderr, limit=300)`
  - `build_alert_delivery_log_line(returncode, log_label, stdout, stderr)`
- `_is_lark_subscribe(pid)` now delegates final cmdline match to:
  - `_watchdog_proc_match.is_lark_subscribe_cmdline(...)`
- Script still keeps `/proc/<pid>/cmdline` read and `OSError -> False` fallback in `_is_lark_subscribe`.
- Pure match semantics remain:
  - return `True` only when cmdline includes `lark-cli` + `event` + `+subscribe`
- Script still retains real process detection and file probes:
  - process probe: `is_running_by_pid_file` / `is_running`
  - file presence and mtime read: `os.path.exists(...)` + `os.path.getmtime(...)` in health-file age path
- Script still retains watchdog pid runtime probes and side effects:
  - real `os.kill` pid probe
  - real `/proc/<pid>/cmdline` read
  - real pid-file read/write
- Script still retains watchdog orphan-wrapper runtime probes and side effects:
  - real `/proc` glob scan
  - real `children` / `status` file read
  - real `_is_lark_subscribe(pid)` checks
  - real `os.kill(..., SIGKILL)`
  - real `time.sleep(0.5)`
  - wrapper body and surrounding high-risk control flow
- Script still keeps compatibility orchestration entrypoints:
  - `_send_manager_alert` entry
  - `notify_manager`
  - `check_once`
  - `log`
  - `sys.exit`
- `_send_manager_alert` still keeps script-side runtime branch and send path:
  - `TESTING` branch
  - real `subprocess.run` send path
- High-risk side-effect boundary remains in script layer:
  - `_send_manager_alert`
  - `notify_manager`
  - `restart_process`
  - `_kill_by_pid_file`
  - `_kill_orphan_lark_subscribers`
  - `_cleanup_pid`
  - `_acquire_pid_lock`
  - `log`
  - `sys.exit`
  - `main`
  - real `subprocess.run` / `subprocess.Popen` / `os.kill` paths

3. Boundary summary
- `scripts/watchdog.py` owns execution and side effects.
- `src/claudeteam/supervision/watchdog_state.py` owns deterministic decision policy.
- `src/claudeteam/supervision/watchdog_specs.py` owns pure process-spec and env-gating policy.
- `src/claudeteam/supervision/watchdog_health.py` owns pure health-file grace/stale decision policy.
- `src/claudeteam/supervision/watchdog_effect_plan.py` owns pure decision->effect-plan policy for `check_once()`.
- `src/claudeteam/supervision/watchdog_alert_request.py` owns pure manager-alert request normalization and command-shaping policy for `_send_manager_alert()`.
- `src/claudeteam/supervision/watchdog_daemon.py` owns pure watchdog pid-lock/liveness policy for `_pid_file_is_live_watchdog()`.
- `src/claudeteam/supervision/watchdog_orphans.py` owns pure router-tree/orphan victim selection policy for `_kill_orphan_lark_subscribers()`.
- `src/claudeteam/supervision/watchdog_messages.py` owns pure burst/cooldown alert-message templates.
- `src/claudeteam/supervision/watchdog_alert_delivery.py` owns pure alert-delivery result formatting and failure-summary policy.
- `src/claudeteam/supervision/watchdog_proc_match.py` owns pure lark subscribe cmdline matching policy.
- `WATCHDOG-GATE-13` covers:
  - `watchdog_orphans` import/injection/contract gate
  - `_kill_orphan_lark_subscribers` wrapper gate
  - retained watchdog entrypoint/state/specs/health/messages/alert-delivery/proc-match/effect-plan/alert-request/daemon gates
- No-live hygiene for this round keeps `workspace/.env/scripts/runtime_config.json/team.json` all absent.
- This split keeps old script entry compatibility while making helper layers testable in no-live gates.

## KANBAN-SVC-11 + KANBAN-GATE-11 Kanban Layer Split

`kanban_sync` now follows a three-layer split with compatibility preserved:

1. Script compatibility shell and command entry
- File: `scripts/kanban_sync.py`
- Keeps legacy entry and patch points:
  - `_lark`
  - `load_tasks`
  - `do_sync`
  - `cmd_init` / `cmd_sync` / `cmd_daemon`
- `main()` delegates argv execution through `src/claudeteam/commands/kanban_sync.py`.
- Daemon live-PID liveness check now delegates to command helper:
  - `_pid_file_is_live_kanban(path)` -> `_kanban_daemon.pid_file_is_live(...)`
- Script daemon shell still owns runtime/compat entrypoints:
  - `_acquire_pid_lock`
  - `_cleanup_pid`
  - `cmd_daemon`
  - `signal`/`atexit` registration
  - `main`
  - script-side `print` / `sys.exit` behavior
- `cmd_init()` now delegates table-init orchestration to
  `kanban_service.ensure_kanban_table_with_run(cfg, _lark, save_cfg)`.
- `do_sync(cfg)` delegates round orchestration to
  `kanban_service.sync_kanban_snapshot_with_run(...)`.
- Script wrapper helpers remain and delegate to service helpers:
  - `fetch_all_agent_status` -> `fetch_all_agent_status_with_run`
  - `get_all_kanban_record_ids` -> `get_all_kanban_record_ids_with_run`
  - `delete_all_kanban_records` -> `delete_all_kanban_records_with_run`
  - `bitable_batch_create` -> `bitable_batch_create_with_run`

2. Pure command parser/dispatcher
- File: `src/claudeteam/commands/kanban_sync.py`
- Owns `parse_argv`, `dispatch`, `run`.
- Handles CLI syntax and handler dispatch without runtime side effects.
- Legacy CLI entry contracts remain stable:
  - `help`
  - `init`
  - `sync`
  - `daemon --interval N`

3. Daemon PID liveness helper boundary
- File: `src/claudeteam/commands/kanban_daemon.py`
- Owns pure PID-text/cmdline/liveness helpers:
  - `parse_pid_text`
  - `is_expected_cmdline`
  - `is_live_pid_probe`
  - `pid_file_is_live`
- Compatibility liveness semantics remain:
  - only `pid` file exists + PID probe alive + cmdline contains `kanban_sync.py` -> live
  - PID reuse/unrelated cmdline -> not live
  - bad PID text -> not live
  - stale PID probe failure -> not live
  - missing PID file -> not live

4. Feishu integration service/projection boundary
- `src/claudeteam/integrations/feishu/kanban_service.py`
  - Owns init + CRUD helpers and round-level sync orchestration via injected `lark_run`.
  - `ensure_kanban_table_with_run(...)` semantics:
    - existing `kanban_table_id`: skip create and return success.
    - create success: write back `cfg["kanban_table_id"]` and persist via injected `save_cfg`.
    - create failure: return failure payload; `cmd_init()` keeps legacy `exit 1` behavior.
- `src/claudeteam/integrations/feishu/kanban_projection.py`
  - Owns row/field/text shaping helpers and projection constants.
- `KANBAN-GATE-11` gate expectations:
  - `kanban_daemon` import/injection/contract gate must hold.
  - daemon PID wrapper gate must hold for script helper delegation and lock flow.
  - `ensure_kanban_table_with_run` import/injection path must not import `kanban_sync` script layer.
  - helper import path must not trigger subprocess/tmux/kill side effects.
  - `cmd_init` service-delegate contract and failure-path (`exit 1`) contract must remain stable.

5. Failure semantics (unchanged)
- Status query failure: skip current round.
- Delete failure: skip current-round write.
- Batch create any-batch failure: stop remaining batches this round.
- These silent-swallow fixes remain script-visible behavior after the split.

## Runtime Data Paths

- Local facts (default core):
  - `workspace/shared/facts/inbox.json`
  - `workspace/shared/facts/status.json`
  - `workspace/shared/facts/logs.jsonl`
- Pending queue:
  - `workspace/shared/.pending_msgs/<agent>.json`
- Router health/cursor:
  - `scripts/.router.cursor`

## Message Flow: Group Message To Agent Response

1. User sends message in Feishu group.
2. `feishu_router.py` receives event (`im.message.receive_v1`).
3. Router filters dedup/bot/cross-chat noise and handles slash commands locally when matched.
4. For business messages, router selects target (`@agent` or default manager).
5. Router runs `wake_on_deliver`, then tries `inject_when_idle`.
6. If not deliverable immediately, message enters pending queue.
7. Queue delivery loop retries and injects when target becomes idle.
8. Agent reads inbox and processes task.
9. Agent replies via `python3 scripts/feishu_msg.py say <agent> "..."`.

## Dispatch/Report Flow: Manager To Worker

1. Manager dispatches via `feishu_msg.py send`.
2. Command writes local inbox fact first (core durability).
3. Group notification is attempted for visibility.
4. Worker receives prompt in tmux or pending queue.
5. Worker updates status/log and sends completion report back to manager.

At command execution level, this flow now passes through:

- `scripts/feishu_msg.py main` -> `src/claudeteam/commands/feishu_msg.py run`
- `run/dispatch` -> mapped legacy `cmd_*` handlers in script shell
- handlers -> messaging service / optional Feishu client adapters

## Local-first vs Remote Boundary (Current State)

Default local core path:

- `inbox/read/status/log/workspace` are local-facts backed.
- Queue durability and replay are local.

Remaining remote-dependent surfaces:

- Feishu group message delivery (`im +messages-send`).
- Workspace/Bitable projections when legacy flags are enabled.
- Kanban projection daemon.
- Owner todo default store (Bitable).
- Feishu docs sync.

## Current Hotspots And Risks

1. Router catch-up polling frequency can create API pressure.
2. Kanban full delete-and-rewrite cycles can amplify Bitable quota usage.
3. Workspace log fan-out can produce non-critical remote write bursts.
4. Duplicate subscriptions or stale router processes can cause duplicate slash responses.

## Dangerous Operations Requiring Confirmation

- Switching Feishu app/profile/credentials for live environments.
- Forcing tmux raw injection bypassing wake/queue safeguards.
- Restarting router/watchdog/kanban daemons without runtime evidence capture.
- Enabling legacy Bitable projection in default core paths.
- Running reset/setup scripts on shared environments without rollback plan.
