# Testing

Last updated: 2026-04-29

## Testing Strategy

ClaudeTeam uses a local-first testing policy:

- Default gate: no-live suite.
- Live smoke: explicit opt-in with isolated credentials and evidence capture.

## Default No-live Gate (Must-pass)

Command:

```bash
python3 tests/run_no_live.py
```

Expectations:

- No real Feishu/Bitable API calls.
- No tmux/docker/network side effects in default path.
- Local fact store and wrapper contracts remain functional.

Relevant files:

- `tests/run_no_live.py`
- `tests/no_live_guard.py`
- `tests/no_bitable_core_smoke.py`
- `tests/static_public_contract_check.py`
- `tests/compat_scripts_entrypoints.py`
- `tests/compat_import_paths.py`
- `tests/watchdog_state_machine_gate.py`

## C3 Compatibility Gate Coverage (R1-ARCH-03)

The C3 command-layer thinning gate focuses on compatibility preservation while
moving parse/dispatch logic into `src/claudeteam/commands/feishu_msg.py`.

1. Script entrypoint and delegation contract
- `tests/compat_scripts_entrypoints.py`
- Verifies `scripts/feishu_msg.py main` delegates to `commands.run(...)`.
- Verifies legacy command usage/error semantics stay stable.
- Verifies `parse_argv` / `dispatch` / `run` contracts when commands module exists.

2. Legacy import path and monkeypatch contract
- `tests/compat_import_paths.py`
- Verifies old import paths remain valid (`feishu_msg`, `local_facts`, `msg_queue`, etc.).
- Verifies monkeypatch points are still effective (`_lark_run`, `_lark_base_*`, helper wrappers).
- Verifies old CLI helper behavior remains callable and signature-compatible where required.

3. Workspace pollution = 0 check
- Compatibility checks assert queue path resolution avoids legacy workspace fallback in controlled state-dir scenarios.
- Goal: command-layer refactor must not silently re-introduce runtime path leakage.

## WATCHDOG-SVC-12 + WATCHDOG-GATE-12 Coverage

Watchdog daemon helper extraction is covered by no-live gates:

1. Watchdog daemon import/injection/contract gate
- `tests/compat_scripts_entrypoints.py::test_watchdog_daemon_helper_import_and_contract_gate_when_present`
- Verifies `src/claudeteam/supervision/watchdog_daemon.py` helper import path does not pull script layer and has no subprocess/tmux/kill side effects.
- Verifies helper contracts for:
  - `parse_pid_text`
  - `is_expected_cmdline`
  - `is_live_pid_probe`
  - `pid_file_is_live`
- Verifies watchdog daemon liveness semantics:
  - live only when pid file exists + pid probe alive + cmdline contains `watchdog.py`
  - PID reuse/unrelated cmdline, bad pid text, missing file -> not live

2. Watchdog `_acquire_pid_lock` wrapper gate
- `tests/compat_scripts_entrypoints.py::test_watchdog_daemon_pid_wrapper_gate`
- Verifies `scripts/watchdog.py::_pid_file_is_live_watchdog(path)` delegates to `_watchdog_daemon.pid_file_is_live(...)`.
- Verifies `_acquire_pid_lock()` still uses helper-gated live checks before writing pid lock.
- Verifies script wrapper still retains:
  - `_acquire_pid_lock`
  - `_cleanup_pid`
  - `main`
  - `log`
  - `sys.exit`
  - real `os.kill`, `/proc/<pid>/cmdline` read, and pid-file read/write paths

3. Workspace pollution = 0 requirement
- `workspace/.env/scripts/runtime_config.json/team.json` must all be `0` (absent).

## WATCHDOG-SVC-13 + WATCHDOG-GATE-13 Coverage

Watchdog service split is covered by no-live gates with compatibility retained:

1. Watchdog orphans import/injection/contract gate
- `tests/compat_scripts_entrypoints.py::test_watchdog_orphans_helper_import_and_contract_gate_when_present`
- Verifies `src/claudeteam/supervision/watchdog_orphans.py` helper import path does not pull script layer and has no subprocess/tmux/kill side effects.
- Verifies helper contracts for:
  - `parse_ppid_from_status_text`
  - `select_router_tree_victims`
  - `select_orphan_victims`
- Verifies victim-selection semantics remain:
  - router live path only selects router-tree subscribe descendants
  - router not live / pid file missing path only selects `PPid == 1` true orphans
  - helper policy always allows wrapper to skip `my_pid`

2. Watchdog `_kill_orphan_lark_subscribers` wrapper gate
- `tests/compat_scripts_entrypoints.py::test_watchdog_orphans_wrapper_gate`
- Verifies `scripts/watchdog.py::_kill_orphan_lark_subscribers()` delegates pure victim selection to orphan helpers.
- Verifies script wrapper still retains:
  - `/proc` glob scan
  - `children` / `status` file read
  - `_is_lark_subscribe(pid)`
  - `os.kill(..., SIGKILL)`
  - `time.sleep(0.5)`
  - wrapper body and high-risk side-effect path
- Verifies runtime semantics remain:
  - router live -> only router-tree subscribe descendants
  - router not live / missing pid file -> only `PPid == 1` true orphans
  - always skip `my_pid`

3. Watchdog daemon import/injection/contract gate (from WATCHDOG-GATE-12, retained)
- See WATCHDOG-SVC-12 + WATCHDOG-GATE-12 Coverage section above for full spec.
- Gate remains active: `test_watchdog_daemon_helper_import_and_contract_gate_when_present`.

4. Watchdog `_acquire_pid_lock` wrapper gate (from WATCHDOG-GATE-12, retained)
- See WATCHDOG-SVC-12 + WATCHDOG-GATE-12 Coverage section above for full spec.
- Gate remains active: `test_watchdog_daemon_pid_wrapper_gate`.

5. Watchdog alert-request import/injection/contract gate (retained)
- `tests/compat_scripts_entrypoints.py::test_watchdog_alert_request_helper_import_and_contract_gate_when_present`
- Verifies `src/claudeteam/supervision/watchdog_alert_request.py` helper import path does not pull script layer and has no subprocess/tmux/kill side effects.
- Verifies helper contracts for:
  - `normalize_alert_message`
  - `normalize_alert_log_label`
  - `build_manager_alert_send_cmd`
  - `build_testing_skip_log_line`

6. Watchdog `_send_manager_alert` wrapper gate (retained)
- `tests/compat_scripts_entrypoints.py::test_watchdog_send_manager_alert_wrapper_gate`
- Verifies `scripts/watchdog.py::_send_manager_alert(...)` delegates request normalization and send-command shaping to helper.
- Verifies script wrapper still retains:
  - `_send_manager_alert` entry
  - `TESTING` branch
  - real `subprocess.run` path
- Verifies helper delegation for:
  - `normalize_alert_message`
  - `normalize_alert_log_label`
  - `build_manager_alert_send_cmd`
  - `build_testing_skip_log_line`

7. Watchdog effect-plan import/injection/contract gate (retained)
- `tests/compat_scripts_entrypoints.py::test_watchdog_effect_plan_helper_import_and_contract_gate_when_present`
- Verifies `src/claudeteam/supervision/watchdog_effect_plan.py` helper import path does not pull script layer and has no subprocess/tmux/kill side effects.
- Verifies helper contracts for:
  - `WatchdogEffectPlan`
  - `build_effect_plan`
  - `EFFECT_CONTINUE` / `EFFECT_ALERT_ONLY` / `EFFECT_RESTART_NOTIFY`
- Verifies decision->effect mapping semantics for watchdog rhythm branches:
  - `healthy` / `healthy_reset` / `cooldown_wait` / `cooldown_ended` / `enter_cooldown` / `restart`

8. Watchdog `check_once()` wrapper/static contract gate (retained)
- `tests/compat_scripts_entrypoints.py::test_watchdog_check_once_effect_plan_wrapper_gate`
- Verifies `scripts/watchdog.py::check_once()` delegates decision->effect-plan mapping to `_watchdog_effect_plan.build_effect_plan(...)`.
- Verifies wrapper behavior keeps script-layer orchestration for:
  - `restart_process`
  - `notify_manager`
  - `_send_manager_alert`
- Verifies no-live guard behavior by forbidding subprocess/os.kill side effects during gate execution.

9. Watchdog proc-match import/injection/contract gate (retained)
- `tests/compat_scripts_entrypoints.py::test_watchdog_proc_match_helper_import_and_contract_gate_when_present`
- Verifies `src/claudeteam/supervision/watchdog_proc_match.py` helper import path does not pull script layer and has no subprocess/tmux/kill side effects.
- Verifies helper contract for:
  - `is_lark_subscribe_cmdline`

10. Watchdog proc-match wrapper gate (retained)
- `tests/compat_scripts_entrypoints.py::test_watchdog_proc_match_wrapper_gate`
- Verifies `scripts/watchdog.py::_is_lark_subscribe(pid)` delegates final cmdline match to helper.
- Verifies script wrapper still reads `/proc/<pid>/cmdline` and keeps `OSError -> False` fallback.
- Verifies pure match semantics remain: cmdline must contain `lark-cli` + `event` + `+subscribe`.

11. Legacy script entrypoint gate (retained)
- `tests/compat_scripts_entrypoints.py::test_watchdog_entrypoint_import_and_main_contract`
- Verifies old entry `scripts/watchdog.py` remains importable and `main()` contract remains compatible in guarded no-live execution.

12. Pure watchdog state machine gate (retained)
- `tests/watchdog_state_machine_gate.py`
- Verifies `src/claudeteam/supervision/watchdog_state.py` exports expected actions and `decide_watchdog_state(...)`.
- Verifies transition behavior across restart/cooldown/recovery paths.
- Verifies helper import and execution path avoids subprocess/os.kill side effects.

13. Watchdog messages helper gate (retained)
- `tests/compat_scripts_entrypoints.py::test_watchdog_messages_helper_import_and_contract_gate_when_present`
- Verifies `src/claudeteam/supervision/watchdog_messages.py` helper import/contracts remain stable for burst/cooldown alert templates.

14. Watchdog alert-delivery helper gate (retained)
- `tests/compat_scripts_entrypoints.py::test_watchdog_alert_delivery_helper_import_and_contract_gate_when_present`
- Verifies `src/claudeteam/supervision/watchdog_alert_delivery.py` helper import/contracts remain stable for send-result classification and failure-summary behavior.

15. Watchdog specs helper gate (retained)
- `tests/compat_scripts_entrypoints.py::test_watchdog_specs_helper_import_and_contract_gate_when_present`
- Verifies `src/claudeteam/supervision/watchdog_specs.py` helper import/contracts remain stable for process-spec and env-gating behavior.

16. Watchdog health helper gate (retained)
- `tests/compat_scripts_entrypoints.py::test_watchdog_health_helper_import_and_contract_gate_when_present`
- Verifies `src/claudeteam/supervision/watchdog_health.py` helper import/contracts remain stable for grace/stale decision behavior.

17. Legacy compatibility gate (import/monkeypatch)
- `tests/compat_import_paths.py` keeps legacy import-path and monkeypatch contracts in no-live mode.
- Scope includes old CLI/import boundaries and patch-based compatibility checks for thin-wrapper migrations.

18. Workspace pollution = 0 requirement
- Gate expectations include no unintended workspace/runtime artifact creation in isolated probe contexts.
- Acceptance output should explicitly report zero pollution (for example `WORKSPACE_EXISTS=0`).
- For watchdog docs acceptance in this round, all of the following must be absent:
  - `workspace`
  - `.env`
  - `scripts/runtime_config.json`
  - `team.json`
- Equivalent compact check form: `workspace/.env/scripts/runtime_config.json/team.json` must all be `0` (not present).

## KANBAN-GATE-11 + KANBAN-SVC-11 Coverage

Kanban split is covered in no-live compatibility gates with legacy entry retained:

1. Kanban command parser/dispatch gate
- `tests/compat_scripts_entrypoints.py::test_kanban_sync_command_parser_dispatch_contract_when_present`
- Verifies `src/claudeteam/commands/kanban_sync.py` parse/dispatch/run contracts.
- Verifies command module gate forbids script-layer side-effect imports and runtime side effects in command path.
- Verifies legacy CLI contract remains for `help/init/sync/daemon --interval`.

2. Kanban script delegation + wrapper contract gate
- `tests/compat_scripts_entrypoints.py::test_kanban_sync_main_delegate_compat_contract`
- `tests/compat_scripts_entrypoints.py::test_kanban_sync_entrypoint_delegate_contract_basic_branches`
- `tests/compat_scripts_entrypoints.py::test_kanban_sync_wrapper_uses_service_contract`
- Verifies `scripts/kanban_sync.py main` delegates via `commands.kanban_sync.run`.
- Verifies `cmd_init()` delegates to
  `kanban_service.ensure_kanban_table_with_run(cfg, _lark, save_cfg)`.
- Verifies `do_sync(cfg)` delegates to `kanban_service.sync_kanban_snapshot_with_run(...)`.
- Verifies legacy shell symbols and helper wrappers remain callable.

3. Kanban daemon import/injection/contract gate
- `tests/compat_scripts_entrypoints.py::test_kanban_daemon_helper_import_and_contract_gate_when_present`
- Verifies `src/claudeteam/commands/kanban_daemon.py` import path does not pull script layer and has no subprocess/tmux/kill side effects.
- Verifies daemon PID liveness compatibility semantics:
  - live only when pid file exists + pid probe alive + cmdline contains `kanban_sync.py`
  - PID reuse/unrelated cmdline, bad pid text, stale pid probe, missing file -> not live

4. Kanban daemon PID wrapper gate
- `tests/compat_scripts_entrypoints.py::test_kanban_sync_daemon_pid_wrapper_gate`
- Verifies `scripts/kanban_sync.py` still delegates daemon pid liveness to `_kanban_daemon.pid_file_is_live(...)`.
- Verifies `_acquire_pid_lock` flow and `cmd_daemon --interval` shell contract remain compatible.

5. Kanban service import/injection gate
- `tests/compat_scripts_entrypoints.py::test_kanban_service_import_and_injection_gate_when_present`
- Verifies `src/claudeteam/integrations/feishu/kanban_service.py` import path does not pull `kanban_sync` script module.
- Verifies helper import path has no subprocess/tmux/kill side effects.
- Verifies `ensure_kanban_table_with_run(...)` contracts:
  - existing table id -> skip create and success return
  - create success -> write back/persist cfg
  - create failure -> return failure payload

6. Kanban cmd_init failure-path contract gate
- `tests/compat_scripts_entrypoints.py::test_kanban_cmd_init_service_delegate_contract`
- Verifies `cmd_init` keeps legacy failure behavior: service ensure failure must print error and `SystemExit(1)`.

7. Legacy compatibility surface remains
- Script-level patch points remain available (`_lark`, `load_tasks`, `do_sync`, `cmd_init/cmd_sync/cmd_daemon`).
- No-live gates treat these as compatibility contracts for thin-wrapper migration.

8. Silent-swallow fix semantics remain under split
- Status query fail -> skip round.
- Delete fail -> skip write this round.
- Any batch create failure -> stop remaining batches this round.

9. Workspace pollution = 0 remains required
- Compatibility acceptance still requires isolated probe output to report `WORKSPACE_EXISTS=0`.
- `tests/compat_import_paths.py` continues to guard legacy import and monkeypatch contracts across thin-wrapper migrations.
- For this round, explicit path checks must stay absent:
  - `workspace`
  - `.env`
  - `scripts/runtime_config.json`
  - `team.json`

## Optional Legacy/Projection Smoke

Only run when explicitly validating legacy adapters or projection behavior:

- `tests/bitable_degradation_smoke.py`
- [bitable_degradation_smoke](bitable_degradation_smoke.md)

## Live Smoke (Explicit Approval)

Live smoke is separate from default tests and requires:

1. Isolated app/profile/group boundary.
2. Approved credentials in project-local paths.
3. End-to-end evidence chain:
- user message
- manager reply
- worker response

Primary references:

- [live_container_smoke](live_container_smoke.md)
- [hardening_profile](hardening_profile.md)

## Test Reporting Template

Each report should include:

1. Scope (no-live / live / legacy).
2. Commands run.
3. Result (pass/fail).
4. Evidence path(s).
5. Remaining risk/blockers.

## C3 Fast Verification Commands

Syntax-only verification for C3 touched files:

```bash
python3 -m py_compile \
  scripts/feishu_msg.py \
  src/claudeteam/commands/feishu_msg.py \
  tests/compat_scripts_entrypoints.py \
  tests/compat_import_paths.py
```

## KANBAN-SVC-11 Fast Verification Commands

Syntax-only verification for KANBAN-SVC-11 / GATE-11 touched files:

```bash
python3 -m py_compile \
  scripts/kanban_sync.py \
  src/claudeteam/commands/*.py \
  src/claudeteam/integrations/feishu/*.py \
  tests/*.py
```

## Common Pitfalls

- Treating legacy smoke as default acceptance.
- Marking live smoke pass without real user-text event evidence.
- Mixing runtime fix and test infra changes in one unbounded patch.
