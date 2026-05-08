# ClaudeTeam Documentation Index

This page is the stable entry point for project documentation.
If you only read one docs page first, read this one.

## Start Here

- [Architecture](ARCHITECTURE.md): current system boundaries and data flow.
- [Operations](OPERATIONS.md): deployment, runtime operations, incident handling.
- [Development](DEVELOPMENT.md): repo conventions and contribution workflow.
- [Testing](TESTING.md): default no-live tests and live smoke boundaries.
- [Troubleshooting](TROUBLESHOOTING.md): common failures and first response actions.
- [Roadmap](ROADMAP.md): documentation and architecture evolution plan.
- [ADRs Index](adrs/README.md): architecture decision records and process.

## Current Alignment Notes

- R1-ARCH-03 C3 command-layer thinning is documented in:
  - [Architecture](ARCHITECTURE.md) (command-layer split and boundaries)
  - [Testing](TESTING.md) (compatibility/no-live gate coverage)
  - [Roadmap](ROADMAP.md) (completed vs pending status)
- WATCHDOG-SVC-12 / WATCHDOG-GATE-12 closure is documented in:
  - [Architecture](ARCHITECTURE.md) (watchdog daemon helper extraction, `_pid_file_is_live_watchdog()` delegation, retained script side-effect boundary)
  - [Testing](TESTING.md) (watchdog daemon import/injection/contract gate + `_acquire_pid_lock` wrapper gate, with pollution=0 requirement)
  - [Roadmap](ROADMAP.md) (completed status and gate closure)
- WATCHDOG-SVC-13 / WATCHDOG-GATE-13 closure is documented in:
  - [Architecture](ARCHITECTURE.md) (watchdog orphan-victim extraction, `_kill_orphan_lark_subscribers()` delegation, retained script high-risk side-effect boundary)
  - [Testing](TESTING.md) (watchdog orphans import/injection/contract + `_kill_orphan_lark_subscribers()` wrapper gate, with retained entrypoint/state/specs/health/messages/alert-delivery/proc-match/effect-plan/alert-request/daemon gates and pollution=0 requirement)
  - [Roadmap](ROADMAP.md) (completed status and next-step constraint)
- KANBAN-GATE-11 / KANBAN-SVC-11 closure is documented in:
  - [Architecture](ARCHITECTURE.md) (`cmd_init`/`do_sync` + daemon pid-live delegation to commands helper, with script compatibility shell boundaries)
  - [Testing](TESTING.md) (kanban daemon import/injection/contract gate + daemon pid wrapper gate + retained entry/compat coverage)
  - [Roadmap](ROADMAP.md) (completed status and follow-up constraints)

## Information Architecture (P0)

The docs structure follows a stable layered model:

1. `README.md` (repo root)
- Public project overview, quick start, and docs entry links.

2. `docs/*.md` (core handbooks)
- Owner-oriented references for architecture, operations, development, testing, and troubleshooting.

3. `docs/adrs/*.md` (decision history)
- Architectural decisions with context, alternatives, and consequences.

4. `docs/*` legacy/specialized files
- Existing topic docs are retained and indexed below; no deletions in P0.

## Legacy Documents (Kept, Not Deleted)

These files existed before the P0 restructure.
They remain valid references, but are now grouped by purpose.

### Governance and Contributor Rules

- [CONTRIBUTING](CONTRIBUTING.md)
- [POLICY](POLICY.md)
- [CODE_STYLE](CODE_STYLE.md)

### Runtime Contracts and Internal Design

- [public_contracts](public_contracts.md)
- [message_rendering_spec](message_rendering_spec.md)
- [slash_commands_system](slash_commands_system.md)
- [toolchain_skill_restructure](toolchain_skill_restructure.md)
- [standard_skill_catalog](standard_skill_catalog.md)

### Smoke and Environment Validation

- [no_bitable_core_smoke](no_bitable_core_smoke.md)
- [bitable_degradation_smoke](bitable_degradation_smoke.md)
- [live_container_smoke](live_container_smoke.md)
- [hardening_profile](hardening_profile.md)

### Bilingual Project Overview

- [README_CN](README_CN.md)

## Maintenance Rule

- Do not delete legacy docs in P0.
- New docs should point to old docs when detail exists.
- If a legacy file is superseded, mark it as legacy and link to the new canonical page.
