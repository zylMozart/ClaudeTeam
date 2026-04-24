#!/usr/bin/env python3
"""No-live public contract checks for restructure planning.

The check verifies files and documented surfaces only. It does not import
runtime modules or call external tools.
"""
from __future__ import annotations

from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]

REQUIRED_FILES = [
    "docs/public_contracts.md",
    "docs/standard_skill_catalog.md",
    "docs/adrs/0001-bitable-kanban-degradation.md",
    "docs/toolchain_skill_restructure.md",
    "scripts/feishu_msg.py",
    "scripts/feishu_router.py",
    "scripts/slash_commands.py",
    "src/claudeteam/runtime/tmux_utils.py",
    "src/claudeteam/cli_adapters/resolve.py",
    "scripts/task_tracker.py",
    "scripts/memory_manager.py",
    "scripts/kanban_sync.py",
    "scripts/start-team.sh",
    "scripts/lib/agent_lifecycle.sh",
]

REQUIRED_SKILLS = [
    "team-communication",
    "task-workflow",
    "feishu-doc-publish",
    "smoke-evidence",
    "rate-limit-triage",
    "runtime-doctor",
    "tmux-boundary-diagnose",
]

PUBLIC_CONTRACT_TOKENS = [
    "python3 scripts/feishu_msg.py",
    "python3 scripts/feishu_router.py --stdin",
    "slash_commands.dispatch(text)",
    "inject_when_idle(session, window, text",
    "python3 -m claudeteam.cli_adapters.resolve",
    "python3 scripts/task_tracker.py",
    "python3 scripts/kanban_sync.py",
]

ADR_TOKENS = [
    "Core facts are local by default",
    "default path must not call Bitable",
    "`npx @larksuite/cli`",
    "explicit opt-in legacy adapter",
    "scripts/task_tracker.py",
    "scripts/kanban_sync.py",
]

LOCAL_CORE_TOKENS = [
    "LocalInboxStore",
    "LocalStatusStore",
    "LocalEventLog",
    "PendingQueue",
    "ProjectionOutbox",
    "default path must not",
]


def fail(message: str) -> None:
    print(f"FAIL: {message}")
    raise SystemExit(1)


def require_file(rel: str) -> Path:
    path = ROOT / rel
    if not path.exists():
        fail(f"missing {rel}")
    return path


def main() -> int:
    for rel in REQUIRED_FILES:
        require_file(rel)

    for skill in REQUIRED_SKILLS:
        require_file(f".claude/skills/{skill}/SKILL.md")

    public_contracts = require_file("docs/public_contracts.md").read_text(encoding="utf-8")
    for token in PUBLIC_CONTRACT_TOKENS:
        if token not in public_contracts:
            fail(f"public contracts missing token: {token}")
    for token in LOCAL_CORE_TOKENS:
        if token not in public_contracts:
            fail(f"public contracts missing local-core token: {token}")

    adr = require_file("docs/adrs/0001-bitable-kanban-degradation.md").read_text(encoding="utf-8")
    for token in ADR_TOKENS:
        if token not in adr:
            fail(f"ADR missing token: {token}")

    smoke_doc = require_file("docs/no_bitable_core_smoke.md").read_text(encoding="utf-8")
    for token in (
        "No-Bitable Core No-Live",
        "`send -> inbox -> read -> status -> log -> workspace`",
        "`python3 tests/run_no_live.py`",
    ):
        if token not in smoke_doc:
            fail(f"no-live smoke doc missing token: {token}")

    print("OK: public contracts and local-core ADR checked")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
