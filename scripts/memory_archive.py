#!/usr/bin/env python3
"""Archive read inbox messages into per-agent workspace archives."""
from __future__ import annotations

import argparse
import dataclasses
import json
import os
import shutil
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
SRC_DIR = PROJECT_ROOT / "src"
if str(SRC_DIR) not in sys.path:
    sys.path.insert(0, str(SRC_DIR))

from claudeteam.storage import local_facts

READ_RETENTION_DAYS = 3
IMPORTANT_RETENTION_DAYS = 7
ARCHIVE_RETENTION_DAYS = 30
BOSS_SENDERS = {"boss", "user", "老板", "群聊", "用户", "open_id", "user-open-id"}
IMPORTANT_PRIORITIES = {"高", "紧急", "urgent", "high", "p0", "p1"}


@dataclasses.dataclass
class ArchiveStats:
    agent: str
    inbox_total: int = 0
    unread: int = 0
    read: int = 0
    archivable: int = 0
    archived: int = 0
    archive_files: int = 0
    cleanup_deleted: int = 0


def now_ms() -> int:
    return int(time.time() * 1000)


def load_team_agents(project_root: Path = PROJECT_ROOT) -> list[str]:
    team_file = Path(os.environ.get("CLAUDETEAM_TEAM_FILE") or project_root / "team.json")
    try:
        data = json.loads(team_file.read_text(encoding="utf-8"))
        return list((data.get("agents") or {}).keys())
    except (OSError, json.JSONDecodeError):
        return sorted(p.name for p in (project_root / "agents").iterdir() if p.is_dir()) if (project_root / "agents").exists() else []


def facts_file() -> Path:
    return local_facts.INBOX_FILE


def read_inbox(path: Path | None = None) -> dict:
    inbox = path or facts_file()
    try:
        return json.loads(inbox.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {"messages": []}


def write_inbox(data: dict, path: Path | None = None) -> None:
    inbox = path or facts_file()
    inbox.parent.mkdir(parents=True, exist_ok=True)
    tmp = inbox.with_suffix(inbox.suffix + ".tmp")
    tmp.write_text(json.dumps(data, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    os.replace(tmp, inbox)


def is_boss_message(message: dict) -> bool:
    sender = str(message.get("from") or message.get("sender") or "").lower()
    if any(marker.lower() in sender for marker in BOSS_SENDERS):
        return True
    content = str(message.get("content") or "")
    return "老板" in content or "用户在群里" in content or "群聊消息" in content


def is_important(message: dict) -> bool:
    priority = str(message.get("priority") or "").strip().lower()
    return priority in IMPORTANT_PRIORITIES or is_boss_message(message)


def message_time_ms(message: dict) -> int:
    value = message.get("read_at") or message.get("created_at") or 0
    try:
        return int(value)
    except (TypeError, ValueError):
        return 0


def retention_days(message: dict) -> int:
    return IMPORTANT_RETENTION_DAYS if is_important(message) else READ_RETENTION_DAYS


def should_archive(message: dict, now: int | None = None) -> bool:
    if not message.get("read"):
        return False
    ts = message_time_ms(message)
    if ts <= 0:
        return False
    age_ms = (now_ms() if now is None else now) - ts
    return age_ms >= retention_days(message) * 86400 * 1000


def month_key(message: dict) -> str:
    ts = (message.get("created_at") or message_time_ms(message) or now_ms()) / 1000
    return datetime.fromtimestamp(ts, tz=timezone.utc).strftime("%Y-%m")


def archive_record(message: dict) -> dict:
    content = str(message.get("content") or "")
    subject = content.strip().splitlines()[0][:80] if content.strip() else "(无主题)"
    return {
        "archived_at": now_ms(),
        "local_id": message.get("local_id"),
        "bitable_record_id": message.get("bitable_record_id", ""),
        "to": message.get("to"),
        "from": message.get("from"),
        "priority": message.get("priority", ""),
        "task_id": message.get("task_id", ""),
        "created_at": message.get("created_at"),
        "read_at": message.get("read_at"),
        "subject": subject,
        "content": content,
    }


def archive_path(project_root: Path, agent: str, message: dict) -> Path:
    return project_root / "agents" / agent / "workspace" / "archive" / month_key(message) / "inbox.jsonl"


def append_archive(project_root: Path, agent: str, message: dict) -> Path:
    path = archive_path(project_root, agent, message)
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8") as f:
        f.write(json.dumps(archive_record(message), ensure_ascii=False, sort_keys=True) + "\n")
    return path


def workspace_archive_files(project_root: Path, agent: str) -> list[Path]:
    root = project_root / "agents" / agent / "workspace" / "archive"
    if not root.exists():
        return []
    return sorted(p for p in root.rglob("*") if p.is_file())


def scan(project_root: Path = PROJECT_ROOT, *, now: int | None = None) -> list[ArchiveStats]:
    agents = load_team_agents(project_root)
    data = read_inbox()
    by_agent = {agent: ArchiveStats(agent=agent) for agent in agents}
    for msg in data.get("messages", []):
        agent = msg.get("to") or "unknown"
        stat = by_agent.setdefault(agent, ArchiveStats(agent=agent))
        stat.inbox_total += 1
        if msg.get("read"):
            stat.read += 1
            if should_archive(msg, now=now):
                stat.archivable += 1
        else:
            stat.unread += 1
    for agent, stat in by_agent.items():
        stat.archive_files = len(workspace_archive_files(project_root, agent))
    return [by_agent[a] for a in sorted(by_agent)]


def archive(project_root: Path = PROJECT_ROOT, *, now: int | None = None) -> list[ArchiveStats]:
    data = read_inbox()
    stats: dict[str, ArchiveStats] = {}
    remaining = []
    for msg in data.get("messages", []):
        agent = msg.get("to") or "unknown"
        stat = stats.setdefault(agent, ArchiveStats(agent=agent))
        stat.inbox_total += 1
        if msg.get("read"):
            stat.read += 1
        else:
            stat.unread += 1
        if should_archive(msg, now=now):
            append_archive(project_root, agent, msg)
            stat.archivable += 1
            stat.archived += 1
        else:
            remaining.append(msg)
    data["messages"] = remaining
    write_inbox(data)
    for agent in load_team_agents(project_root):
        stats.setdefault(agent, ArchiveStats(agent=agent))
    for agent, stat in stats.items():
        stat.archive_files = len(workspace_archive_files(project_root, agent))
    return [stats[a] for a in sorted(stats)]


def cleanup(project_root: Path = PROJECT_ROOT, *, now: int | None = None, retention_days: int = ARCHIVE_RETENTION_DAYS) -> list[ArchiveStats]:
    cutoff = (now_ms() if now is None else now) - retention_days * 86400 * 1000
    stats = {agent: ArchiveStats(agent=agent) for agent in load_team_agents(project_root)}
    archive_roots = sorted((project_root / "agents").glob("*/workspace/archive"))
    for root in archive_roots:
        agent = root.parents[1].name
        stat = stats.setdefault(agent, ArchiveStats(agent=agent))
        for file_path in sorted(p for p in root.rglob("*") if p.is_file()):
            try:
                mtime_ms = int(file_path.stat().st_mtime * 1000)
            except OSError:
                continue
            if mtime_ms < cutoff:
                file_path.unlink()
                stat.cleanup_deleted += 1
        for dir_path in sorted((p for p in root.rglob("*") if p.is_dir()), reverse=True):
            try:
                dir_path.rmdir()
            except OSError:
                pass
        stat.archive_files = len(workspace_archive_files(project_root, agent))
    return [stats[a] for a in sorted(stats)]


def report(project_root: Path = PROJECT_ROOT, *, now: int | None = None) -> str:
    rows = scan(project_root, now=now)
    lines = ["Agent memory archive status"]
    for s in rows:
        lines.append(
            f"{s.agent}: inbox={s.inbox_total} unread={s.unread} read={s.read} "
            f"archivable={s.archivable} archive_files={s.archive_files}"
        )
    return "\n".join(lines)


def print_stats(rows: list[ArchiveStats]) -> None:
    for s in rows:
        extra = f" archived={s.archived}" if s.archived else ""
        deleted = f" cleanup_deleted={s.cleanup_deleted}" if s.cleanup_deleted else ""
        print(
            f"{s.agent}: inbox={s.inbox_total} unread={s.unread} read={s.read} "
            f"archivable={s.archivable} archive_files={s.archive_files}{extra}{deleted}"
        )


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="ClaudeTeam inbox memory archive utility")
    sub = parser.add_subparsers(dest="cmd", required=True)
    sub.add_parser("scan")
    sub.add_parser("archive")
    clean_p = sub.add_parser("cleanup")
    clean_p.add_argument("--days", type=int, default=ARCHIVE_RETENTION_DAYS)
    sub.add_parser("report")
    args = parser.parse_args(argv)

    if args.cmd == "scan":
        print_stats(scan())
    elif args.cmd == "archive":
        print_stats(archive())
    elif args.cmd == "cleanup":
        print_stats(cleanup(retention_days=args.days))
    elif args.cmd == "report":
        print(report())
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
