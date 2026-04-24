# Documentation Roadmap

Last updated: 2026-04-24

## Scope

This roadmap is for documentation information-architecture cleanup only.
No runtime code changes are included in this document.

## P0 Baseline Review (README + docs current issues)

### README current issues

1. Architecture narrative at top still implies Bitable is the core fact source.
2. Documentation entry is not centralized; readers jump between scattered files.
3. Runtime architecture, operations, testing, and troubleshooting are not clearly separated.

### docs/ current issues

1. No stable docs index (`docs/README.md`) for navigation.
2. Topic files are mixed by type (policy, smoke, design, contracts) without hierarchy.
3. Legacy documents and current canonical docs are not explicitly distinguished.
4. ADR usage exists but lacks index/process guidance.
5. Newcomers cannot quickly answer: how the system works, how to operate it, how to test it, and how to debug it.

## Target Open-source-grade Information Architecture

```text
README.md                  # public entry + architecture stance + docs links
└── docs/
    ├── README.md          # docs index (canonical navigation)
    ├── ARCHITECTURE.md    # system boundary + flow + risk boundary
    ├── OPERATIONS.md      # run/operate/incident handbook
    ├── DEVELOPMENT.md     # contribution and implementation workflow
    ├── TESTING.md         # no-live and live-smoke testing strategy
    ├── TROUBLESHOOTING.md # symptom -> checks -> action
    ├── ROADMAP.md         # docs evolution and milestones
    └── adrs/
        ├── README.md      # ADR index + writing process
        └── 0001-*.md      # decision records
```

Design principles:

1. One page per core question (what/how/run/test/fix/decide).
2. Stable top-level entry and explicit canonical docs.
3. Legacy docs preserved in index, not deleted in P0.
4. Operationally useful structure before visual/perfection work.

## P0 Deliverables

Delivered in this wave:

- `docs/README.md`
- `docs/ARCHITECTURE.md`
- `docs/OPERATIONS.md`
- `docs/DEVELOPMENT.md`
- `docs/TESTING.md`
- `docs/TROUBLESHOOTING.md`
- `docs/ROADMAP.md`
- `docs/adrs/README.md`
- Root `README.md` updated with docs navigation and current architecture stance.

## Architecture Track Status

### R1-ARCH-03 / C3 (Completed)

Status: completed and documented.

Scope closed in this stage:

1. `feishu_msg` command entry split
- Added `src/claudeteam/commands/feishu_msg.py` with `parse_argv` / `dispatch` / `run`.
- `scripts/feishu_msg.py main` now delegates through `commands.run(...)`.

2. Compatibility retained
- Legacy `cmd_*` symbols and patch points remain available in script shell.
- Compatibility tests cover old CLI usage, import paths, and monkeypatch contracts.

3. Validation outcome
- C3 no-live compatibility gates reported pass.
- Runtime workspace-path leakage target remained zero under compatibility checks.

### R1-ARCH-04 (Pending, Not Started Here)

Status: pending / to be decided by architecture and implementation owners.

Planned direction (subject to approval):

1. Continue reducing script-shell responsibility while preserving external CLI contracts.
2. Consolidate service/client boundaries and reduce cross-layer coupling.
3. Expand contract tests before removing any remaining legacy surfaces.

This document intentionally does not mark R1-ARCH-04 as completed.

### WATCHDOG-SVC-12 / WATCHDOG-GATE-12 (Completed)

Status: completed and documented.

Scope closed in this stage:

1. Watchdog daemon helper extraction and delegation
- Added `src/claudeteam/supervision/watchdog_daemon.py`.
- `scripts/watchdog.py::_pid_file_is_live_watchdog(path)` now delegates to:
  - `_watchdog_daemon.pid_file_is_live(...)`
- Daemon liveness semantics owned by helper:
  - `parse_pid_text`
  - `is_expected_cmdline`
  - `is_live_pid_probe`
  - `pid_file_is_live`
- Semantics: live only when pid file exists + pid probe alive + cmdline contains `watchdog.py`.
  PID reuse / unrelated cmdline / bad pid text / missing file -> not live.

2. Script compatibility retained
- `scripts/watchdog.py` retains all runtime side-effect entrypoints:
  - `_acquire_pid_lock` / `_cleanup_pid` / `main` / `log` / `sys.exit`
  - real `os.kill`, `/proc/<pid>/cmdline` read, and pid-file read/write paths

3. Gate closure
- No-live gates added:
  - `tests/compat_scripts_entrypoints.py::test_watchdog_daemon_helper_import_and_contract_gate_when_present`
  - `tests/compat_scripts_entrypoints.py::test_watchdog_daemon_pid_wrapper_gate`
- Reported as QA pass for this scope.

4. Pollution requirement
- `workspace/.env/scripts/runtime_config.json/team.json` must all be `0` (absent).

### WATCHDOG-SVC-13 / WATCHDOG-GATE-13 (Completed)

Status: completed and documented.

Scope closed in this stage:

1. Watchdog helper extraction and delegation
- Added `src/claudeteam/supervision/watchdog_orphans.py`.
- `scripts/watchdog.py::_kill_orphan_lark_subscribers()` now delegates pure victim selection to:
  - `_watchdog_orphans.parse_ppid_from_status_text(...)`
  - `_watchdog_orphans.select_router_tree_victims(...)`
  - `_watchdog_orphans.select_orphan_victims(...)`
- Existing helper delegation remains:
  - state decisions -> `src/claudeteam/supervision/watchdog_state.py::decide_watchdog_state(...)`
  - config/spec/env gating -> `src/claudeteam/supervision/watchdog_specs.py`
  - health/grace/stale decisions -> `src/claudeteam/supervision/watchdog_health.py`
  - proc cmdline pure match -> `src/claudeteam/supervision/watchdog_proc_match.py::is_lark_subscribe_cmdline(...)`
  - decision->effect pure mapping -> `src/claudeteam/supervision/watchdog_effect_plan.py::build_effect_plan(...)`
  - manager alert request shaping -> `src/claudeteam/supervision/watchdog_alert_request.py`
  - watchdog pid-file/liveness policy -> `src/claudeteam/supervision/watchdog_daemon.py`
  - orphan victim selection policy -> `src/claudeteam/supervision/watchdog_orphans.py`
  - burst/cooldown alert text -> `src/claudeteam/supervision/watchdog_messages.py`
  - alert delivery result handling -> `src/claudeteam/supervision/watchdog_alert_delivery.py`

2. Script compatibility retained
- `scripts/watchdog.py` continues to own runtime process probing and file observation:
  - `is_running_by_pid_file` / `is_running`
  - health file existence and mtime read (`os.path.exists`, `os.path.getmtime`)
- `scripts/watchdog.py::_is_lark_subscribe(pid)` still owns:
  - `/proc/<pid>/cmdline` read
  - `OSError -> False` fallback
  - pure match semantics: only cmdline containing `lark-cli` + `event` + `+subscribe` returns `True`
- `scripts/watchdog.py` still keeps compatibility orchestration entrypoints:
  - `_send_manager_alert` entry
  - `notify_manager`
  - `check_once`
  - `log`
  - `sys.exit`
- Watchdog rhythm semantics remain:
  - `healthy`
  - `healthy_reset`
  - `cooldown_wait`
  - `cooldown_ended`
  - `enter_cooldown`
  - `restart`
  - `all_ok`
- `_send_manager_alert` still keeps runtime branch and real send path:
  - `TESTING` branch
  - real `subprocess.run` send path
- Watchdog daemon liveness semantics remain:
  - live only when pid file exists + pid probe alive + cmdline contains `watchdog.py`
  - PID reuse/unrelated cmdline -> not live
  - bad pid text -> not live
  - missing pid file -> not live
- Orphan-kill semantics remain:
  - router live -> only kill router-tree subscribe descendants
  - router not live or pid file missing -> only kill `PPid == 1` true orphans
  - always skip `my_pid`
- `scripts/watchdog.py` continues to own legacy shell high-risk side-effect entrypoints:
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
  - real `/proc/<pid>/cmdline` read and pid-file read/write paths
  - real `/proc` glob plus `children` / `status` reads
  - real `time.sleep(0.5)`

3. Gate closure
- Added/updated no-live gates in:
  - `tests/compat_scripts_entrypoints.py::test_watchdog_orphans_helper_import_and_contract_gate_when_present`
  - `tests/compat_scripts_entrypoints.py::test_watchdog_orphans_wrapper_gate`
  - `tests/compat_scripts_entrypoints.py::test_watchdog_effect_plan_helper_import_and_contract_gate_when_present`
  - `tests/compat_scripts_entrypoints.py::test_watchdog_check_once_effect_plan_wrapper_gate`
  - `tests/compat_scripts_entrypoints.py::test_watchdog_entrypoint_import_and_main_contract`
  - `tests/watchdog_state_machine_gate.py`
- Existing watchdog helper gates remain:
  - `tests/compat_scripts_entrypoints.py::test_watchdog_daemon_helper_import_and_contract_gate_when_present`
  - `tests/compat_scripts_entrypoints.py::test_watchdog_daemon_pid_wrapper_gate`
  - `tests/compat_scripts_entrypoints.py::test_watchdog_alert_request_helper_import_and_contract_gate_when_present`
  - `tests/compat_scripts_entrypoints.py::test_watchdog_send_manager_alert_wrapper_gate`
  - `tests/compat_scripts_entrypoints.py::test_watchdog_proc_match_helper_import_and_contract_gate_when_present`
  - `tests/compat_scripts_entrypoints.py::test_watchdog_proc_match_wrapper_gate`
  - `tests/compat_scripts_entrypoints.py::test_watchdog_alert_delivery_helper_import_and_contract_gate_when_present`
  - `tests/compat_scripts_entrypoints.py::test_watchdog_messages_helper_import_and_contract_gate_when_present`
  - `tests/compat_scripts_entrypoints.py::test_watchdog_specs_helper_import_and_contract_gate_when_present`
  - `tests/compat_scripts_entrypoints.py::test_watchdog_health_helper_import_and_contract_gate_when_present`
- Reported as QA pass for this scope.

4. Pollution requirement
- `workspace/.env/scripts/runtime_config.json/team.json` must all be `0` (absent).

Next-step constraint for future watchdog modularization:

- Any next cut (including possible R1-ARCH-04 scope) should avoid runtime/credential/container changes.
- Keep changes bounded to module layering, contracts, and no-live gate coverage unless explicitly re-scoped.

### KANBAN-GATE-11 / KANBAN-SVC-11 (Completed)

Status: completed and documented.

Scope closed in this stage:

1. Kanban service orchestration extraction
- Added `src/claudeteam/integrations/feishu/kanban_service.py`.
- `scripts/kanban_sync.py cmd_init` now delegates to
  `kanban_service.ensure_kanban_table_with_run(cfg, _lark, save_cfg)`.
- `scripts/kanban_sync.py do_sync` now delegates to
  `kanban_service.sync_kanban_snapshot_with_run(...)`.
- Script Bitable helper wrappers remain as compatibility shell and delegate to service helpers via injected `lark_run`.
- Added `src/claudeteam/commands/kanban_daemon.py`.
- `scripts/kanban_sync.py` daemon live pid decision now delegates to
  `_kanban_daemon.pid_file_is_live(...)`.

2. Command-entry split retained
- `scripts/kanban_sync.py main` delegates to `src/claudeteam/commands/kanban_sync.py`.
- Script compatibility shell and patch points remain.
- Script daemon shell retains:
  - `_acquire_pid_lock` / `_cleanup_pid` / `cmd_daemon`
  - `signal` / `atexit` wiring
  - `main`, `print`, `sys.exit`
- Legacy CLI contract retained:
  - `help`
  - `init`
  - `sync`
  - `daemon --interval`

3. Gate closure
- Entry delegation/compat gates covered in `tests/compat_scripts_entrypoints.py`.
- `kanban_daemon` import/injection/contract gate covered in:
  - `tests/compat_scripts_entrypoints.py::test_kanban_daemon_helper_import_and_contract_gate_when_present`
- daemon pid wrapper gate covered in:
  - `tests/compat_scripts_entrypoints.py::test_kanban_sync_daemon_pid_wrapper_gate`
- `ensure_kanban_table_with_run` import/injection gate covered in
  `tests/compat_scripts_entrypoints.py` to enforce no script-layer imports and no import-time subprocess/tmux/kill side effects.
- `cmd_init` delegate and failure-path contracts are covered by compat/no-live gates:
  service ensure failure must keep `exit 1` behavior in script shell.
- Reported as QA pass for this scope.

Next-step constraint for further `scripts/kanban_sync.py` thinning:

- Keep script patch points stable (`_lark`, `load_tasks`, `do_sync`, `cmd_init/cmd_sync/cmd_daemon`).
- Keep daemon liveness semantics stable:
  - only pid file exists + pid probe alive + cmdline contains `kanban_sync.py` => live
  - PID reuse/bad pid/missing file => not live
- Preserve existing failure semantics:
  - status query fail -> skip round
  - delete fail -> skip write
  - any batch create failure -> stop remaining batches that round

## Legacy Strategy (Explicit)

- Legacy docs are retained in-place.
- New docs index classifies old files by purpose.
- No deletions in P0; deprecation markers can be added in later phases.

## Next Phases

### P1 (quality and consistency)

1. Align tone and naming across English/Chinese docs.
2. Add architecture diagrams and sequence diagrams.
3. Add ownership metadata and update cadence per doc.

### P2 (automation and governance)

1. Add docs lint/check links into CI.
2. Require ADR for architecture-impacting changes.
3. Add docs drift checks against runtime contracts.
