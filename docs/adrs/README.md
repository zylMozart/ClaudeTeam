# ADR Index

Last updated: 2026-04-29

## Purpose

Architecture Decision Records (ADRs) track decisions that affect long-term
system structure, boundaries, and compatibility.

Use ADRs to capture:

- context and constraints,
- options considered,
- decision made,
- consequences and follow-up.

## Current ADRs

- [0001-bitable-kanban-degradation](0001-bitable-kanban-degradation.md)

## ADR Authoring Rules

1. Create file name: `NNNN-short-title.md`.
2. Include: `Date`, `Status`, `Owner`.
3. Keep sections: `Context`, `Decision`, `Consequences`.
4. Link related docs/contracts/tests when relevant.

## Status Guidance

- `draft`: under discussion, not yet governing.
- `accepted`: approved and governing for implementation.
- `superseded`: replaced by a newer ADR (must link replacement).

## Relationship To Other Docs

- `docs/ARCHITECTURE.md` describes current state.
- ADRs explain why state was chosen and what tradeoffs were accepted.
