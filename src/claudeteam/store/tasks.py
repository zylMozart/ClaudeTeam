"""Local task store — coordination cards across agents.

One JSON file (`$CLAUDETEAM_STATE_DIR/tasks.json`) with shape:
    {"tasks": [...], "_meta": {"last_id": N}}

Each task:
    {id, title, description, assignee, creator,
     status, created_at, updated_at, completed_at}

Pure file-locked CRUD; lifecycle (assignment, completion, etc.) is whatever
the agents agree on — the store is opinion-free.

Status vocabulary: 待处理 / 进行中 / 已完成 / 已取消
"""
from __future__ import annotations

import json
import time
from pathlib import Path

from claudeteam.runtime import paths
from claudeteam.util import atomic_write_text, flock


VALID_STATUSES = {"待处理", "进行中", "已完成", "已取消"}
DEFAULT_STATUS = "待处理"
TERMINAL_STATUSES = {"已完成", "已取消"}


def _file() -> Path:
    return paths.state_dir() / "tasks.json"


def _now_ms() -> int:
    return int(time.time() * 1000)


def _locked():
    return flock(_file().with_suffix(".lock"))


def _load() -> dict:
    p = _file()
    if not p.exists():
        return {"tasks": [], "_meta": {"last_id": 0}}
    return json.loads(p.read_text(encoding="utf-8"))


def _save(data: dict) -> None:
    atomic_write_text(_file(), json.dumps(data, ensure_ascii=False, indent=2) + "\n")


# ── public API ────────────────────────────────────────────────────


def create(assignee: str, title: str, *,
           description: str = "", creator: str = "") -> str:
    """Create a new task; return its task_id (T-<n>)."""
    if not title.strip():
        raise ValueError("title cannot be empty")
    with _locked():
        data = _load()
        data["_meta"]["last_id"] = data["_meta"].get("last_id", 0) + 1
        tid = f"T-{data['_meta']['last_id']}"
        now = _now_ms()
        data.setdefault("tasks", []).append({
            "id": tid,
            "title": title.strip(),
            "description": description,
            "assignee": assignee,
            "creator": creator,
            "status": DEFAULT_STATUS,
            "created_at": now,
            "updated_at": now,
            "completed_at": None,
        })
        _save(data)
        return tid


def update(task_id: str, *, status: str | None = None,
           assignee: str | None = None, title: str | None = None,
           description: str | None = None) -> bool:
    """Apply non-None fields. Returns False if task_id not found."""
    if status is not None and status not in VALID_STATUSES:
        raise ValueError(f"invalid status: {status} (valid: {sorted(VALID_STATUSES)})")
    with _locked():
        data = _load()
        for task in data.get("tasks", []):
            if task["id"] != task_id:
                continue
            if status is not None:
                task["status"] = status
                if status in TERMINAL_STATUSES:
                    task["completed_at"] = _now_ms()
                else:
                    task["completed_at"] = None
            if assignee is not None:
                task["assignee"] = assignee
            if title is not None:
                task["title"] = title.strip()
            if description is not None:
                task["description"] = description
            task["updated_at"] = _now_ms()
            _save(data)
            return True
    return False


def get(task_id: str) -> dict | None:
    for task in _load().get("tasks", []):
        if task["id"] == task_id:
            return task
    return None


def list_tasks(*, status: str | None = None,
               assignee: str | None = None) -> list[dict]:
    """Return tasks filtered by status / assignee, sorted by id."""
    rows = list(_load().get("tasks", []))
    if status is not None:
        rows = [t for t in rows if t.get("status") == status]
    if assignee is not None:
        rows = [t for t in rows if t.get("assignee") == assignee]
    rows.sort(key=lambda t: int(t["id"].split("-")[1]) if "-" in t["id"] else 0)
    return rows
