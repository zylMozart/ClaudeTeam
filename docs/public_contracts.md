# Public Contracts And Wrapper Compatibility

Date: 2026-04-23
Owner: toolsmith
Status: P0 local-core contract draft for restructure worktree

## Purpose

This document defines the public command and import contracts that must survive
the first toolchain restructure phase. The current implementation may later move
logic into `src/claudeteam/`, but these wrappers remain stable until manager
explicitly approves a user-facing migration.

The P0 default path is local-first and no-live by design: message delivery,
inbox/read state, employee status, task facts, kanban facts, and workspace logs
must use local durable store/log files by default. The default path must not
call Bitable, `lark-cli`, or `npx @larksuite/cli`. Any Feishu/Bitable behavior
left during this phase is a legacy adapter or manual display/audit export and
must be explicitly opted in.

## Contract Table

| Surface | Public Contract | Current Path | Compatibility Requirement | No-Live Test |
|---|---|---|---|---|
| Team messaging CLI | `python3 scripts/feishu_msg.py {send,direct,say,inbox,read,status,log,workspace}` | `scripts/feishu_msg.py` | Preserve command names, argument order, exit semantics for full/partial success, and human-readable output. `send`, `direct`, `inbox`, `read`, and `status` must use local facts by default and must not call Bitable or `lark-cli` unless an explicit legacy/live flag or env gate is set. | Static wrapper existence; no-live command fixtures with Bitable/lark-cli blocked. |
| Local inbox store | `LocalInboxStore` equivalent: append, list unread/all, mark read by local id, preserve legacy id lookup only as compatibility metadata. | `scripts/local_facts.py` or future local core module | Durable local write happens before notification. Read must never mark messages implicitly. Duplicate delivery is prevented by local message ids. | Temp-dir fixture with Bitable create/search/update/list failing. |
| Local status store | `LocalStatusStore` equivalent: set/get/list latest employee status. | `scripts/local_facts.py` or future local core module | Status writes are local facts. Remote write failure cannot make status command fail or pretend a remote projection is current. | Temp-dir fixture with remote adapter hard-failing. |
| Local event log | `LocalEventLog` equivalent: append/list workspace, delivery, status, and error events. | `scripts/local_facts.py` or future local core module | Workspace log and manager patrol evidence use local log by default. Remote audit/export is optional. | Temp-dir fixture proving log append/list without credentials. |
| Pending queue / outbox | `PendingQueue` for tmux/employee notification retry; `ProjectionOutbox` only for explicit legacy export. | `scripts/msg_queue.py` or future local queue module | Bitable is not a normal projection target. Queue failures must be visible and cannot erase durable inbox/status facts. | No-live fixture with tmux and remote adapters blocked. |
| Feishu router daemon | `python3 scripts/feishu_router.py --stdin` and no-arg self-start mode | `scripts/feishu_router.py` | Preserve stdin NDJSON mode, cursor behavior, slash prefilter, routing, wake/pending queue contract. | Static wrapper existence; future no-live `handle_event()` fixture tests. |
| Slash dispatcher | `dispatch(text) -> (matched, reply)` | `src/claudeteam/commands/slash/standalone.py` | Preserve tuple contract and reply forms: `str` or `{"text": ..., "card": ...}`. | Future unit tests calling dispatch without tmux/Feishu. |
| tmux injection | `inject_when_idle(session, window, text, ...) -> InjectionResult` | `src/claudeteam/runtime/tmux_utils.py` | Preserve bool compatibility, `error`, `target`, `unsafe_input`, `busy_before`, `submitted`, `residual_visible`. | Future unit tests with subprocess mocked. |
| CLI adapter shell bridge | `python3 -m claudeteam.cli_adapters.resolve <agent> <attr> ...` | `src/claudeteam/cli_adapters/resolve.py` | Preserve `spawn_cmd`, `resume_cmd`, `ready_markers`, `busy_markers`, `process_name`, `thinking_init_hint`. | Static wrapper existence; future fake team.json tests. |
| Team start | `bash scripts/start-team.sh [--lazy-mode|--no-lazy-mode]` | `scripts/start-team.sh` | Preserve tmux session creation, router/kanban/watchdog window names, lazy-mode flags. | Static wrapper existence; live smoke only after manager approval. |
| Agent lifecycle | `bash scripts/lib/agent_lifecycle.sh {spawn|suspend|wake} <agent>` | `scripts/lib/agent_lifecycle.sh` | Preserve verb names and return-code semantics. | Static wrapper existence; future fake-process tests. |
| Task tracker | `python3 scripts/task_tracker.py {create,update,list,get}` | `scripts/task_tracker.py` | Preserve local JSON task schema and CLI verbs. | Static wrapper existence; local temp-file fixture later. |
| Memory manager | `python3 scripts/memory_manager.py {init,write,append,read,update-core,note,archive,index}` | `scripts/memory_manager.py` | Preserve file layout under `agents/<agent>/memory`. | Static wrapper existence; temp-dir fixture later. |
| Kanban view/export | `python3 scripts/kanban_sync.py {init,sync,daemon}` during compatibility phase | `scripts/kanban_sync.py` | Not a canonical task/status source. Default facts come from local task/status/log stores. Any Bitable sync must be opt-in legacy/export behavior and disabled in default no-live verification. | Static wrapper existence; mocked legacy adapter tests only. |

## Local Core P0 Acceptance

The P0 acceptance command chain is:

```bash
python3 scripts/feishu_msg.py send <agent> manager "msg" 高
python3 scripts/feishu_msg.py inbox <agent>
python3 scripts/feishu_msg.py read <local_id>
python3 scripts/feishu_msg.py status <agent> 进行中 "task"
```

With Bitable create/search/update/list patched to fail and `lark-cli`/`npx`
blocked, this chain must still exit 0 for local success, show the sent message
in inbox, mark exactly the requested local message as read, and persist the
status locally. It must not fake read state, fake status state, or duplicate
delivery records.

## Wrapper Compatibility Checklist

Before changing any public wrapper:

1. Record the current command syntax in this document.
2. Add or update a no-live test that proves the wrapper path still exists and
   the default path does not call Bitable, `lark-cli`, or `npx @larksuite/cli`.
3. If behavior changes, add a mocked fixture test for the behavior before editing.
4. Keep `scripts/*.py` import paths working for at least one migration phase.
5. Do not make users switch to `ct` or `python -m claudeteam` during Phase 1.
6. Do not move router, lifecycle, or Feishu delivery code until no-live tests and
   explicit live-smoke gates exist.
7. Keep legacy Feishu/Bitable export behind an explicit opt-in flag or
   environment gate; never run it as part of the default command chain.

## Future Package Wrapper Shape

When a script is ready to thin out, the wrapper should look like this:

```python
#!/usr/bin/env python3
from claudeteam.<domain>.<module> import main

if __name__ == "__main__":
    main()
```

For modules imported by existing code, add a compatibility re-export first:

```python
from claudeteam.tmux.injection import InjectionResult, inject_when_idle
```

Do not combine module movement with semantic fixes in the same change.
