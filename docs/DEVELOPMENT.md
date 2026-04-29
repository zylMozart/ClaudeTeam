# Development

Last updated: 2026-04-29

## Purpose

This guide defines how to make changes in ClaudeTeam without breaking runtime contracts.

## Source Layout

- `scripts/`: runtime command wrappers and daemons.
- `agents/`: role prompts, identity, workspace artifacts.
- `docs/`: handbooks, ADRs, and specialized references.
- `tests/`: no-live default suite and smoke helpers.
- `workspace/shared/`: runtime data and evidence artifacts.

## Development Principles

1. Preserve stable CLI surfaces (`feishu_msg.py`, router, queue, watchdog).
2. Keep core paths local-first and no-live safe by default.
3. Treat Feishu/Bitable calls as explicit adapters, not hidden defaults.
4. Prefer small, testable, reversible changes.

## Recommended Local Workflow

1. Create a focused branch/worktree.
2. Update docs/contracts before broad behavior changes.
3. Run default no-live tests.
4. Validate one targeted runtime scenario.
5. Attach evidence paths in the manager report.

## Core Runtime Surfaces

- Messaging: `scripts/feishu_msg.py`
- Routing: `scripts/feishu_router.py`
- Queue: `scripts/msg_queue.py`
- Facts storage: `scripts/local_facts.py`
- Watchdog: `scripts/watchdog.py`

## Tmux Input Submission Helper

When a script or debugging shell types text into an agent's tmux pane, a single
`tmux send-keys -t <pane> Enter` is **not** enough to submit it. Claude Code
in INSERT mode treats a bare Enter inside the input buffer as a literal
newline; only Enter on a settled input line triggers submission.

The fact-of-record submit sequence lives in
`src/claudeteam/runtime/tmux_utils.py`:

- **Internal**: `_press_submit(target, keys=None)` — used by
  `inject_when_idle`. Default keys: `("Enter", "C-m")` with a 0.2 s gap.
- **Public**: `submit_to_pane(session, window, keys=None)` — the same
  sequence, but takes a `(session, window)` pair so ad-hoc tools and tests
  stay on the same path.

Per-CLI adapters under `claudeteam.cli_adapters` expose a `submit_keys()`
method so a CLI that needs a different sequence (e.g. Codex/Gemini → just
`["Enter"]`) can opt out without forking the helper.

```python
from claudeteam.runtime.tmux_utils import submit_to_pane

# 默认序列 (Claude Code)
submit_to_pane("claudeteam", "manager")

# 用 CLI 适配器自带的序列
from claudeteam.cli_adapters import adapter_for_agent
adapter = adapter_for_agent("manager")
submit_to_pane("claudeteam", "manager", keys=adapter.submit_keys())
```

If you find yourself reimplementing the submit dance with raw `subprocess.run
(["tmux", "send-keys", ...])`, replace it with `submit_to_pane` — drift
between callers is how Issue #7 in `docs/DEPLOYMENT_ISSUES.md` got logged.

## Documentation Workflow

When behavior changes, update these pages together:

1. `docs/ARCHITECTURE.md` (boundary and flow)
2. `docs/OPERATIONS.md` (operator actions)
3. `docs/TESTING.md` (acceptance and regression command)
4. `docs/TROUBLESHOOTING.md` (new failure mode)
5. ADR file under `docs/adrs/` if it is an architectural decision

## Compatibility Rules

- Do not break command names or argument contracts without migration notes.
- Keep legacy docs but mark superseded sections and link canonical pages.
- If introducing new env flags, document default/opt-in behavior explicitly.

## Related References

- [public_contracts](public_contracts.md)
- [CONTRIBUTING](CONTRIBUTING.md)
- [CODE_STYLE](CODE_STYLE.md)
- [ADRs Index](adrs/README.md)
