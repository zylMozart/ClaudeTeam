"""Local file-backed fact store for ClaudeTeam.

One source of truth on a host for:
- inbox       (per-agent message queue, JSON)
- status      (latest per-agent status snapshot, JSON)
- heartbeats  (last-seen-active timestamp per agent, JSON)
- log         (append-only event log, JSONL)

All paths derive from `$CLAUDETEAM_STATE_DIR` re-read on every call so tests
get isolation by setting the env, no monkey-patching required. All JSON
writes go through `util.write_json` (atomic tmp+rename via flock).

Originally pulled from the old `claudeteam.storage.local_facts` (~187 LOC).
Each public function corresponds to one CLI surface: `claudeteam send` →
`append_message`, `inbox` → `list_messages`, `read` → `mark_read`,
`status` → `upsert_status` / `get_status`, `team` → `list_all_statuses`
+ `all_heartbeats`, `log`/`workspace` → `append_log` / `list_logs`.
"""
from __future__ import annotations

import json
import uuid
from pathlib import Path

from claudeteam.runtime.paths import facts_dir as _facts_dir
from claudeteam.util import flock, now_ms, read_json, write_json


def _inbox_file() -> Path:
    return _facts_dir() / "inbox.json"


def _status_file() -> Path:
    return _facts_dir() / "status.json"


def _log_file() -> Path:
    return _facts_dir() / "logs.jsonl"


def _new_id(prefix: str) -> str:
    return f"{prefix}_{now_ms()}_{uuid.uuid4().hex[:10]}"


def _locked():
    return flock(_facts_dir() / ".facts.lock")


# ── inbox ─────────────────────────────────────────────────────────────


def append_message(to: str, frm: str, content: str, *,
                   priority: str = "中", task_id: str = "") -> str:
    """Append a message to the inbox; return its local id."""
    with _locked():
        path = _inbox_file()
        data = read_json(path, {"messages": []})
        local_id = _new_id("msg")
        data.setdefault("messages", []).append({
            "local_id": local_id,
            "to": to,
            "from": frm,
            "content": str(content or ""),
            "priority": priority,
            "task_id": task_id,
            "created_at": now_ms(),
            "read": False,
            "read_at": None,
        })
        write_json(path, data)
        return local_id


def list_messages(agent: str, *, unread_only: bool = False) -> list[dict]:
    data = read_json(_inbox_file(), {"messages": []})
    rows = [m for m in data.get("messages", []) if m.get("to") == agent]
    if unread_only:
        rows = [m for m in rows if not m.get("read")]
    return sorted(rows, key=lambda m: m.get("created_at", 0))


def mark_read(local_id: str) -> bool:
    with _locked():
        path = _inbox_file()
        data = read_json(path, {"messages": []})
        for msg in data.get("messages", []):
            if msg.get("local_id") == local_id:
                msg["read"] = True
                msg["read_at"] = now_ms()
                write_json(path, data)
                return True
    return False


# ── status ────────────────────────────────────────────────────────────


def upsert_status(agent: str, status: str, task: str, *, blocker: str = "") -> None:
    with _locked():
        path = _status_file()
        data = read_json(path, {"agents": {}})
        data.setdefault("agents", {})[agent] = {
            "agent": agent,
            "status": status,
            "task": task,
            "blocker": blocker,
            "updated_at": now_ms(),
        }
        write_json(path, data)


def get_status(agent: str) -> dict | None:
    return read_json(_status_file(), {"agents": {}}).get("agents", {}).get(agent)


def list_all_statuses() -> list[dict]:
    """Latest status row for every agent that ever upserted, sorted by name."""
    data = read_json(_status_file(), {"agents": {}})
    return [data["agents"][a] for a in sorted(data.get("agents", {}))]


# ── heartbeats ────────────────────────────────────────────────────────


def _heartbeat_file() -> Path:
    return _facts_dir() / "heartbeats.json"


def touch_heartbeat(agent: str) -> None:
    """Record `agent` as alive right now. Cheap; safe to call from any command."""
    if not agent:
        return
    with _locked():
        path = _heartbeat_file()
        data = read_json(path, {})
        data[agent] = now_ms()
        write_json(path, data)


def get_heartbeat(agent: str) -> int | None:
    return read_json(_heartbeat_file(), {}).get(agent)


def all_heartbeats() -> dict[str, int]:
    return dict(read_json(_heartbeat_file(), {}))


# ── log ───────────────────────────────────────────────────────────────


def append_log(agent: str, kind: str, content: str, *, ref: str = "") -> str:
    local_id = _new_id("log")
    row = {
        "local_id": local_id,
        "agent": agent,
        "type": kind,
        "content": str(content or ""),
        "ref": ref,
        "created_at": now_ms(),
    }
    with _locked():
        path = _log_file()
        path.parent.mkdir(parents=True, exist_ok=True)
        with path.open("a", encoding="utf-8") as fh:
            fh.write(json.dumps(row, ensure_ascii=False) + "\n")
    return local_id


def list_logs(agent: str, *, limit: int = 20) -> list[dict]:
    path = _log_file()
    if not path.exists():
        return []
    rows = []
    with path.open(encoding="utf-8") as fh:
        for raw in fh:
            raw = raw.strip()
            if not raw:
                continue
            row = json.loads(raw)
            if row.get("agent") == agent:
                rows.append(row)
    return rows[-limit:]
