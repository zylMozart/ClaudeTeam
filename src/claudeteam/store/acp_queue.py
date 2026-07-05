"""File-backed per-agent delivery queue for ACP-runner agents.

This queue IS the delivery state machine (the fix for "tmux inject is
best-effort with no ACK"):

    pending → prompting → done | failed          (kind="prompt")
    pending → done                               (kind="cancel" / "stop")

Producers (any process — the router's deliver step, a worker's
`claudeteam send` shell-out) `enqueue()` and return immediately.  The
single consumer — the AcpHost service living in the router daemon —
`claim_next()`s a row, drives the ACP turn, and `settle()`s it with the
stopReason.  Because rows live on disk, a router crash mid-turn leaves
the row in `prompting`; `recover_stuck()` flips it back to `pending` on
host restart (at-least-once delivery — a turn may re-run, a message is
never silently lost).

Layout: `$CLAUDETEAM_STATE_DIR/acp/<agent>/queue.json`, atomic writes
under a per-agent flock, same pattern as `store/local_facts`.
"""
from __future__ import annotations

from pathlib import Path

from claudeteam.runtime.paths import acp_agent_dir
from claudeteam.util import flock, now_ms, read_json, write_json


PENDING = "pending"
PROMPTING = "prompting"
DONE = "done"
FAILED = "failed"

# A prompt row that crashed the host this many times is parked as FAILED
# instead of being retried forever (a poison message must not wedge the
# whole queue).
MAX_ATTEMPTS = 3

# Completed rows kept per agent for `claudeteam logs` / debugging; the
# queue file stays bounded no matter how chatty the team is.
KEEP_SETTLED = 200


def _queue_file(agent: str) -> Path:
    return acp_agent_dir(agent) / "queue.json"


def _locked(agent: str):
    d = acp_agent_dir(agent)
    d.mkdir(parents=True, exist_ok=True)
    return flock(d / ".queue.lock")


def _load(agent: str) -> dict:
    return read_json(_queue_file(agent), {"rows": []})


def _save(agent: str, data: dict) -> None:
    rows = data.get("rows", [])
    settled = [r for r in rows if r.get("state") in (DONE, FAILED)]
    if len(settled) > KEEP_SETTLED:
        drop = set(id(r) for r in settled[:-KEEP_SETTLED])
        data["rows"] = [r for r in rows if id(r) not in drop]
    write_json(_queue_file(agent), data)


def enqueue(agent: str, text: str, *, kind: str = "prompt",
            sender: str = "", local_id: str = "") -> str:
    """Append a row and return its qid. `local_id` links back to the
    inbox row so the ACK trail is joinable end-to-end."""
    import uuid
    qid = f"q_{now_ms()}_{uuid.uuid4().hex[:8]}"
    with _locked(agent):
        data = _load(agent)
        data.setdefault("rows", []).append({
            "qid": qid,
            "kind": kind,
            "text": str(text or ""),
            "sender": sender,
            "local_id": local_id,
            "state": PENDING,
            "attempts": 0,
            "stop_reason": "",
            "error": "",
            "enq_at": now_ms(),
            "upd_at": now_ms(),
        })
        _save(agent, data)
    return qid


def claim_next(agent: str, *, kind: str = "prompt") -> dict | None:
    """Atomically claim the oldest pending row of `kind` (mark it
    `prompting`, bump attempts) and return a copy. None when idle."""
    with _locked(agent):
        data = _load(agent)
        for row in data.get("rows", []):
            if row.get("state") == PENDING and row.get("kind") == kind:
                row["state"] = PROMPTING
                row["attempts"] = int(row.get("attempts", 0)) + 1
                row["upd_at"] = now_ms()
                _save(agent, data)
                return dict(row)
    return None


def take_control_rows(agent: str) -> list[dict]:
    """Claim-and-settle every pending non-prompt row (cancel / stop).
    Control ops are consumed immediately — they never sit behind a long
    prompt backlog."""
    out: list[dict] = []
    with _locked(agent):
        data = _load(agent)
        for row in data.get("rows", []):
            if row.get("state") == PENDING and row.get("kind") != "prompt":
                row["state"] = DONE
                row["upd_at"] = now_ms()
                out.append(dict(row))
        if out:
            _save(agent, data)
    return out


def settle(agent: str, qid: str, state: str, *,
           stop_reason: str = "", error: str = "") -> bool:
    """Mark a claimed row done/failed with its outcome. The ACK."""
    with _locked(agent):
        data = _load(agent)
        for row in data.get("rows", []):
            if row.get("qid") == qid:
                row["state"] = state
                row["stop_reason"] = stop_reason
                row["error"] = error
                row["upd_at"] = now_ms()
                _save(agent, data)
                return True
    return False


def requeue(agent: str, qid: str, *, error: str = "") -> bool:
    """Put a claimed row back to pending for another attempt (turn errored
    but the message must not be lost). The explicit name for what would
    otherwise read as a confusing settle-to-PENDING."""
    return settle(agent, qid, PENDING, error=error)


def recover_stuck(agent: str) -> list[dict]:
    """On host restart: any row still `prompting` was in flight when the
    previous host died. Re-arm it (→ pending) unless it already burned
    MAX_ATTEMPTS, in which case park it as failed. Returns the re-armed
    rows so the host can log what it's about to replay."""
    rearmed: list[dict] = []
    with _locked(agent):
        data = _load(agent)
        changed = False
        for row in data.get("rows", []):
            if row.get("state") != PROMPTING:
                continue
            changed = True
            if int(row.get("attempts", 0)) >= MAX_ATTEMPTS:
                row["state"] = FAILED
                row["error"] = f"gave up after {row['attempts']} interrupted attempts"
            else:
                row["state"] = PENDING
                rearmed.append(dict(row))
            row["upd_at"] = now_ms()
        if changed:
            _save(agent, data)
    return rearmed


def rows(agent: str, *, state: str = "") -> list[dict]:
    """All rows (oldest first), optionally filtered by state."""
    data = _load(agent)
    out = data.get("rows", [])
    if state:
        out = [r for r in out if r.get("state") == state]
    return [dict(r) for r in out]


def has_inflight(agent: str) -> bool:
    """True iff a prompt row is currently claimed (turn in flight)."""
    return any(r.get("kind") == "prompt" for r in rows(agent, state=PROMPTING))
