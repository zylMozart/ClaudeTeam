#!/usr/bin/env python3
"""Unit tests for scripts/memory_archive.py."""
from __future__ import annotations

import json
import os
import sys
import tempfile
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
for p in (ROOT / "scripts", ROOT / "src"):
    if str(p) not in sys.path:
        sys.path.insert(0, str(p))

import memory_archive
from claudeteam.storage import local_facts

DAY_MS = 86400 * 1000
NOW = 1777249200000


class PatchFacts:
    def __init__(self, root: Path):
        self.root = root
        self.old = (local_facts.FACTS_DIR, local_facts.INBOX_FILE, local_facts.STATUS_FILE, local_facts.LOCK_FILE)

    def __enter__(self):
        facts = self.root / "facts"
        local_facts.FACTS_DIR = facts
        local_facts.INBOX_FILE = facts / "inbox.json"
        local_facts.STATUS_FILE = facts / "status.json"
        local_facts.LOCK_FILE = facts / ".facts.lock"
        memory_archive.local_facts.FACTS_DIR = facts
        memory_archive.local_facts.INBOX_FILE = facts / "inbox.json"
        return facts

    def __exit__(self, exc_type, exc, tb):
        local_facts.FACTS_DIR, local_facts.INBOX_FILE, local_facts.STATUS_FILE, local_facts.LOCK_FILE = self.old
        memory_archive.local_facts.INBOX_FILE = self.old[1]


def write_team(root: Path):
    (root / "team.json").write_text(json.dumps({
        "session": "S",
        "agents": {"manager": {}, "coder": {}, "devops": {}},
    }), encoding="utf-8")
    for agent in ("manager", "coder", "devops"):
        (root / "agents" / agent / "workspace").mkdir(parents=True, exist_ok=True)


def msg(local_id, to="coder", frm="manager", priority="中", days=4, read=True, content="hello"):
    ts = NOW - days * DAY_MS
    return {
        "local_id": local_id,
        "to": to,
        "from": frm,
        "content": content,
        "priority": priority,
        "task_id": "",
        "created_at": ts,
        "read": read,
        "read_at": ts if read else None,
        "bitable_record_id": "",
    }


def write_inbox(messages):
    local_facts.INBOX_FILE.parent.mkdir(parents=True, exist_ok=True)
    local_facts.INBOX_FILE.write_text(json.dumps({"messages": messages}, ensure_ascii=False), encoding="utf-8")


def read_inbox_ids():
    return [m["local_id"] for m in json.loads(local_facts.INBOX_FILE.read_text(encoding="utf-8"))["messages"]]


def with_env(root: Path):
    old = os.environ.get("CLAUDETEAM_TEAM_FILE")
    os.environ["CLAUDETEAM_TEAM_FILE"] = str(root / "team.json")
    return old


def restore_env(old):
    if old is None:
        os.environ.pop("CLAUDETEAM_TEAM_FILE", None)
    else:
        os.environ["CLAUDETEAM_TEAM_FILE"] = old


def test_scan_counts_archivable_messages():
    with tempfile.TemporaryDirectory() as td:
        root = Path(td)
        write_team(root)
        old = with_env(root)
        try:
            with PatchFacts(root):
                write_inbox([
                    msg("old-read", days=4),
                    msg("unread", days=10, read=False),
                    msg("recent-read", days=1),
                ])
                rows = {s.agent: s for s in memory_archive.scan(root, now=NOW)}
                assert rows["coder"].inbox_total == 3
                assert rows["coder"].unread == 1
                assert rows["coder"].archivable == 1
        finally:
            restore_env(old)


def test_archive_moves_old_read_message_and_writes_metadata():
    with tempfile.TemporaryDirectory() as td:
        root = Path(td)
        write_team(root)
        old = with_env(root)
        try:
            with PatchFacts(root):
                write_inbox([msg("old-read", days=4, content="Subject line\nbody"), msg("unread", days=10, read=False)])
                rows = {s.agent: s for s in memory_archive.archive(root, now=NOW)}
                assert rows["coder"].archived == 1
                assert read_inbox_ids() == ["unread"]
                files = list((root / "agents" / "coder" / "workspace" / "archive").rglob("inbox.jsonl"))
                assert len(files) == 1
                record = json.loads(files[0].read_text(encoding="utf-8").splitlines()[0])
                assert record["local_id"] == "old-read"
                assert record["from"] == "manager"
                assert record["subject"] == "Subject line"
                assert record["content"].endswith("body")
        finally:
            restore_env(old)


def test_important_message_kept_until_seven_days():
    with tempfile.TemporaryDirectory() as td:
        root = Path(td)
        write_team(root)
        old = with_env(root)
        try:
            with PatchFacts(root):
                write_inbox([
                    msg("high-6d", priority="高", days=6),
                    msg("boss-6d", frm="user-open-id", days=6),
                    msg("normal-4d", priority="中", days=4),
                ])
                memory_archive.archive(root, now=NOW)
                assert read_inbox_ids() == ["high-6d", "boss-6d"]
        finally:
            restore_env(old)


def test_important_message_archived_after_seven_days():
    with tempfile.TemporaryDirectory() as td:
        root = Path(td)
        write_team(root)
        old = with_env(root)
        try:
            with PatchFacts(root):
                write_inbox([msg("high-8d", priority="高", days=8)])
                memory_archive.archive(root, now=NOW)
                assert read_inbox_ids() == []
        finally:
            restore_env(old)


def test_cleanup_deletes_archive_files_older_than_retention():
    with tempfile.TemporaryDirectory() as td:
        root = Path(td)
        write_team(root)
        archive_dir = root / "agents" / "coder" / "workspace" / "archive" / "2026-03"
        archive_dir.mkdir(parents=True)
        old_file = archive_dir / "inbox.jsonl"
        old_file.write_text("{}\n", encoding="utf-8")
        old_mtime = (NOW - 31 * DAY_MS) / 1000
        os.utime(old_file, (old_mtime, old_mtime))
        rows = {s.agent: s for s in memory_archive.cleanup(root, now=NOW, retention_days=30)}
        assert rows["coder"].cleanup_deleted == 1
        assert not old_file.exists()


def test_report_outputs_agent_status():
    with tempfile.TemporaryDirectory() as td:
        root = Path(td)
        write_team(root)
        old = with_env(root)
        try:
            with PatchFacts(root):
                write_inbox([msg("old-read", days=4)])
                text = memory_archive.report(root, now=NOW)
                assert "Agent memory archive status" in text
                assert "coder:" in text
                assert "archivable=1" in text
        finally:
            restore_env(old)


def main() -> int:
    tests = [v for k, v in sorted(globals().items()) if k.startswith("test_")]
    passed = failed = 0
    for fn in tests:
        try:
            fn()
            print(f"  ok {fn.__name__}")
            passed += 1
        except Exception as e:
            print(f"  fail {fn.__name__}: {e}")
            failed += 1
    print(f"\nmemory archive tests: {passed}/{passed + failed} passed")
    return 0 if failed == 0 else 1


if __name__ == "__main__":
    raise SystemExit(main())
