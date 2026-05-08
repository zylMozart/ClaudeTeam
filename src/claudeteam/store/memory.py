"""Per-agent durable memory.

Each agent gets a `memory.jsonl` under their state dir
(`facts/<agent>/memory.jsonl`) — append-only structured notes that
survive across tmux pane restarts and `/clear` cycles.

Why not reuse `local_facts.append_log`? Logs are an audit trail
(every action). Memory is a curated subset — what an agent should
*re-read* on wake to keep continuity. Keeping them separate lets us:
  - inject memory (NOT logs) into the identity init prompt without
    flooding the worker with audit minutiae.
  - rate-limit memory growth (`_MAX_PER_AGENT = 200`) without
    forcing log truncation.

Each entry is a tiny dict:
  {kind, content, ref?, created_at}

`kind` is a short tag (see `KNOWN_KINDS`):
  `task_assigned` / `task_completed` / `learning` / `blocker` /
  `decision` / `note`
Convention, not enforced — `append` accepts any string but soft-warns
to stderr when `kind` falls outside KNOWN_KINDS so the schema doesn't
fragment into free-form labels (`fyi`, `important!!!`, `mood-of-day`)
that hurt `recall` scannability.

API surface:
  `append(agent, kind, content, *, ref="")`  → write 1 entry
  `list_recent(agent, *, limit=20)`          → list, oldest-first
  `clear(agent)`                              → drop all entries
  `clear_kind(agent, kind)`                   → drop one slice
  `render_for_prompt(agent, *, limit=20)`     → markdown for init prompt
  `all_agents_with_memory()`                  → iterator for /health audit
  `kinds_summary()` / `kinds_sorted()`        → KNOWN_KINDS pretty-prints
"""
from __future__ import annotations

import json
from typing import Iterable

from claudeteam.runtime import paths
from claudeteam.util import flock, now_ms, read_jsonl


_MAX_PER_AGENT = 200  # cap retained entries; oldest get dropped on overflow

# Convention vocabulary for memory entry `kind`. Not enforced — `append`
# accepts any string so future kinds can land without a code change —
# but unknown kinds get a soft stderr warning so boss `recall` reading
# doesn't get a long-tail of free-form labels (`fyi`, `important!!!`,
# `mood-of-day`) that fragment the schema. Round-106 added the warn.
KNOWN_KINDS: tuple[str, ...] = (
    "task_assigned",
    "task_completed",
    "learning",
    "blocker",
    "decision",
    "note",
)


def kinds_summary() -> str:
    """`' / '`-joined list of KNOWN_KINDS — used by `claudeteam remember /
    recall / forget` USAGE strings. Round-119: extracted from the three
    CLI commands so the separator (and any future kinds) flows from one
    place."""
    return " / ".join(KNOWN_KINDS)


def kinds_sorted() -> list[str]:
    """Sorted list of KNOWN_KINDS — used by slash card handlers
    (`/recall`, `/forget`) for in-card display. Round-120: 4 sites in
    `feishu/slash.py` previously called `sorted(memory.KNOWN_KINDS)`
    inline; centralising here keeps the alphabetical-display contract
    in one place (Feishu boss reading two cards expects the same
    order)."""
    return sorted(KNOWN_KINDS)


def warn_unknown_kind(kind: str) -> None:
    """If `kind` is non-empty and not in KNOWN_KINDS, emit a one-line
    stderr nudge with the convention list. No-op for empty / known
    kinds.

    Round-156: extracted from `commands/recall` + `commands/forget`,
    which inlined the same `warn(f"⚠️ --kind {kind!r} not in known
    kinds ({sorted(KNOWN_KINDS)}); proceeding anyway")` block. The
    slash-card siblings (`feishu/slash._handle_recall/_handle_forget`)
    don't use this — they embed the warning into a card body string
    with different formatting per command, so they keep their
    inline branches.
    """
    if kind and kind not in KNOWN_KINDS:
        from claudeteam.util import warn
        warn(f"⚠️  --kind {kind!r} not in known kinds "
             f"({sorted(KNOWN_KINDS)}); proceeding anyway")


def _agent_dir(agent: str):
    return paths.facts_dir() / agent


def _memory_file(agent: str):
    return _agent_dir(agent) / "memory.jsonl"


def _locked(agent: str):
    return flock(_agent_dir(agent) / ".memory.lock")


def append(agent: str, kind: str, content: str, *, ref: str = "") -> dict:
    """Append a memory entry. Returns the persisted record (for caller logging).

    The append is fcntl-locked so concurrent writers from different
    panes don't interleave bytes mid-line. Caller passes `ref` (a
    message_id, task_id, etc.) when the memory is tied to an external
    artefact — it is rendered verbatim into the recall view, not parsed.

    Round-106: when `kind` isn't in KNOWN_KINDS, print a one-line
    stderr warning suggesting a known kind. Doesn't reject the write
    (free-form is sometimes the right call), just nudges so recall
    output stays scannable.
    """
    if kind and kind not in KNOWN_KINDS:
        import sys
        print(f"  ⚠️ memory.append: unknown kind {kind!r} for {agent} — "
              f"convention is {sorted(KNOWN_KINDS)}; entry written "
              f"anyway",
              file=sys.stderr)
    entry = {
        "kind": str(kind),
        "content": str(content or ""),
        "ref": str(ref or ""),
        "created_at": now_ms(),
    }
    _agent_dir(agent).mkdir(parents=True, exist_ok=True)
    path = _memory_file(agent)
    with _locked(agent):
        # Read existing (corrupt-tolerant via util.read_jsonl), append,
        # truncate from front if over cap, write back atomically.
        # Append-only file mode would be faster but we need the
        # truncate-from-front step to keep memory size bounded.
        rows = read_jsonl(path)
        rows.append(entry)
        if len(rows) > _MAX_PER_AGENT:
            rows = rows[-_MAX_PER_AGENT:]
        path.write_text(
            "\n".join(json.dumps(r, ensure_ascii=False) for r in rows) + "\n",
            encoding="utf-8",
        )
    return entry


def list_recent(agent: str, *, limit: int = 20) -> list[dict]:
    """Return up to `limit` most recent memory entries, oldest-first
    (so the agent reads them in chronological order)."""
    return read_jsonl(_memory_file(agent))[-limit:]


def list_recent_filtered(agent: str, *,
                         kind: str = "", limit: int = 20) -> list[dict]:
    """Return up to `limit` most recent entries, oldest-first.

    Round-141: when `kind` is set, scans the FULL memory window for
    matches and slices to `limit` afterwards — so a hot agent with many
    unrelated notes still surfaces rare kinds (e.g. one resolved
    blocker among 100 task_completed rows). When `kind` is empty,
    behaves identically to `list_recent` so callers can use this as
    the single entry point regardless of whether they filter.

    Extracted from `commands/recall.py` + `feishu/slash._handle_recall`,
    which inlined the same fetch-then-filter dance and both reached
    into the module-private `_MAX_PER_AGENT` to size the pre-filter
    window.
    """
    if kind:
        all_rows = list_recent(agent, limit=_MAX_PER_AGENT)
        return [r for r in all_rows if r.get("kind") == kind][-limit:]
    return list_recent(agent, limit=limit)


def clear(agent: str) -> int:
    """Wipe an agent's memory file. Returns the number of dropped entries.

    Used by `claudeteam reset` (the whole-state nuke) and operationally
    when an agent's history is poisoned and starting fresh is cheaper
    than triaging which memories are stale."""
    path = _memory_file(agent)
    if not path.exists():
        return 0
    n = sum(1 for _ in path.read_text(encoding="utf-8").splitlines() if _.strip())
    path.unlink()
    return n


def clear_kind(agent: str, kind: str) -> int:
    """Drop only entries with `kind == <kind>` from `agent`'s memory.

    Returns the number of dropped entries (0 if the file is missing or
    no entries match). Round-111: scalpel inside the scalpel — `forget
    --kind blocker` lets boss / manager wipe one slice (e.g. resolved
    blockers) while keeping decisions / learnings intact.

    Reads + filters + atomic-rewrites under the same flock the append
    path uses, so concurrent writers can't see a partial state.
    """
    path = _memory_file(agent)
    if not path.exists():
        return 0
    with _locked(agent):
        rows = read_jsonl(path)
        kept = [r for r in rows if r.get("kind") != kind]
        dropped = len(rows) - len(kept)
        if dropped == 0:
            return 0
        if not kept:
            # All entries matched the filter — remove the file entirely
            # so list_recent treats this agent as fresh (matches `clear`'s
            # "empty memory == no file" invariant).
            path.unlink()
        else:
            path.write_text(
                "\n".join(json.dumps(r, ensure_ascii=False)
                          for r in kept) + "\n",
                encoding="utf-8",
            )
        return dropped


def render_for_prompt(agent: str, *, limit: int = 20) -> str:
    """Format `agent`'s recent memory as a markdown block suitable for
    injecting into the identity init prompt.

    Empty memory → empty string (callers should branch on `if memory:`).
    Each entry renders as one bullet line: `- [<kind>] <content> (ref=<ref>)`
    with the ref suffix omitted when empty.
    """
    rows = list_recent(agent, limit=limit)
    if not rows:
        return ""
    lines = ["## 既往记忆（按时间）"]
    for r in rows:
        suffix = f" (ref={r['ref']})" if r.get("ref") else ""
        lines.append(f"- [{r.get('kind', '?')}] {r.get('content', '')}{suffix}")
    return "\n".join(lines)


def all_agents_with_memory() -> Iterable[str]:
    """Yield agent names that have a memory file. For health / audit."""
    facts = paths.facts_dir()
    if not facts.exists():
        return
    for child in sorted(facts.iterdir()):
        if child.is_dir() and (child / "memory.jsonl").exists():
            yield child.name
