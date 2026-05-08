#!/usr/bin/env python3
"""Local fact store for core ClaudeTeam state.

This module is deliberately small and file-backed. It is the default core fact
source for inbox/read/status/workspace logs. Feishu/Bitable integrations, if
kept, are explicit opt-in legacy adapters and must not gate local facts.
"""
from __future__ import annotations

import contextlib
import json
import os
import time
import uuid
from pathlib import Path

from claudeteam.runtime.paths import PROJECT_ROOT


def _default_facts_dir() -> Path:
    state_dir = os.environ.get("CLAUDETEAM_STATE_DIR", "").strip()
    if state_dir:
        return Path(state_dir) / "facts"
    return Path(PROJECT_ROOT) / "workspace" / "shared" / "facts"


FACTS_DIR = Path(
    os.environ.get("CLAUDETEAM_FACTS_DIR")
    or _default_facts_dir()
)
INBOX_FILE = FACTS_DIR / "inbox.json"
STATUS_FILE = FACTS_DIR / "status.json"
LOG_FILE = FACTS_DIR / "logs.jsonl"
LOCK_FILE = FACTS_DIR / ".facts.lock"


@contextlib.contextmanager
def _locked():
    FACTS_DIR.mkdir(parents=True, exist_ok=True)
    with LOCK_FILE.open("a+") as f:
        try:
            import fcntl

            fcntl.flock(f.fileno(), fcntl.LOCK_EX)
            yield
        finally:
            try:
                import fcntl

                fcntl.flock(f.fileno(), fcntl.LOCK_UN)
            except Exception:
                pass


def _read_json(path: Path, default):
    if not path.exists():
        return default
    with path.open(encoding="utf-8") as f:
        return json.load(f)


def _write_json(path: Path, data) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    with tmp.open("w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)
        f.write("\n")
    os.replace(tmp, path)


def now_ms() -> int:
    return int(time.time() * 1000)


def new_local_id(prefix: str = "local") -> str:
    return f"{prefix}_{now_ms()}_{uuid.uuid4().hex[:10]}"


def append_message(
    to_agent: str,
    from_agent: str,
    content: str,
    priority: str = "中",
    *,
    task_id: str = "",
    bitable_record_id: str = "",
) -> str:
    """Persist a full inbox message locally and return its local id."""
    with _locked():
        data = _read_json(INBOX_FILE, {"messages": []})
        local_id = new_local_id("msg")
        data.setdefault("messages", []).append({
            "local_id": local_id,
            "to": to_agent,
            "from": from_agent,
            "content": str(content or ""),
            "priority": priority,
            "task_id": task_id,
            "created_at": now_ms(),
            "read": False,
            "read_at": None,
            "bitable_record_id": bitable_record_id,
        })
        _write_json(INBOX_FILE, data)
        return local_id


def attach_bitable_record(local_id: str, bitable_record_id: str) -> bool:
    if not bitable_record_id:
        return False
    with _locked():
        data = _read_json(INBOX_FILE, {"messages": []})
        for msg in data.get("messages", []):
            if msg.get("local_id") == local_id:
                msg["bitable_record_id"] = bitable_record_id
                _write_json(INBOX_FILE, data)
                return True
    return False


def list_messages(agent_name: str, *, unread_only: bool = False) -> list[dict]:
    data = _read_json(INBOX_FILE, {"messages": []})
    rows = [m for m in data.get("messages", []) if m.get("to") == agent_name]
    if unread_only:
        rows = [m for m in rows if not m.get("read")]
    return sorted(rows, key=lambda m: m.get("created_at", 0))


def mark_read(record_id: str) -> bool:
    """Mark a local message as read by local id or legacy mirrored Bitable id."""
    with _locked():
        data = _read_json(INBOX_FILE, {"messages": []})
        for msg in data.get("messages", []):
            if record_id in (msg.get("local_id"), msg.get("bitable_record_id")):
                msg["read"] = True
                msg["read_at"] = now_ms()
                _write_json(INBOX_FILE, data)
                return True
    return False


def upsert_status(agent_name: str, status: str, task: str, blocker: str = "") -> None:
    with _locked():
        data = _read_json(STATUS_FILE, {"agents": {}})
        data.setdefault("agents", {})[agent_name] = {
            "agent": agent_name,
            "status": status,
            "task": task,
            "blocker": blocker,
            "updated_at": now_ms(),
        }
        _write_json(STATUS_FILE, data)


def get_status(agent_name: str) -> dict | None:
    data = _read_json(STATUS_FILE, {"agents": {}})
    return data.get("agents", {}).get(agent_name)


def append_log(agent_name: str, log_type: str, content: str, ref: str = "") -> str:
    local_id = new_local_id("log")
    row = {
        "local_id": local_id,
        "agent": agent_name,
        "type": log_type,
        "content": str(content or ""),
        "ref": ref,
        "created_at": now_ms(),
    }
    with _locked():
        FACTS_DIR.mkdir(parents=True, exist_ok=True)
        with LOG_FILE.open("a", encoding="utf-8") as f:
            f.write(json.dumps(row, ensure_ascii=False) + "\n")
    return local_id


def list_logs(agent_name: str, *, limit: int = 20) -> list[dict]:
    if not LOG_FILE.exists():
        return []
    rows = []
    with LOG_FILE.open(encoding="utf-8") as f:
        for raw in f:
            raw = raw.strip()
            if not raw:
                continue
            row = json.loads(raw)
            if row.get("agent") == agent_name:
                rows.append(row)
    return rows[-limit:]
