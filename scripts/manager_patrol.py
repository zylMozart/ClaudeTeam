#!/usr/bin/env python3
"""Manager patrol reporter.

Collects team pane status plus local task progress, then reports to Feishu.
Run once by default, or use --loop --interval 300 for a 5-minute patrol.
"""
import argparse
import json
import subprocess
import sys
import time
from datetime import datetime, timezone, timedelta
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
SCRIPTS = ROOT / "scripts"
TASKS_FILE = ROOT / "workspace" / "shared" / "tasks" / "tasks.json"
BJ_TZ = timezone(timedelta(hours=8))

sys.path.insert(0, str(SCRIPTS))
from team_command import collect_team_status  # noqa: E402


def _unfinished_tasks(limit=8):
    if not TASKS_FILE.is_file():
        return []
    try:
        data = json.loads(TASKS_FILE.read_text(encoding="utf-8"))
    except Exception:
        return [{"task_id": "tasks", "status": "异常", "assignee": "-", "title": "tasks.json 读取失败"}]

    tasks = [
        t for t in data.get("tasks", [])
        if t.get("status") not in ("已完成", "已取消")
    ]
    tasks.sort(key=lambda t: t.get("updated_at", ""), reverse=True)
    return tasks[:limit]


def build_report():
    now = datetime.now(BJ_TZ).strftime("%Y-%m-%d %H:%M")
    rows = collect_team_status()
    busy = [r for r in rows if r["status"] == "busy"]
    offline = [r for r in rows if r["status"] == "offline"]

    lines = [f"5分钟巡视 · {now} 北京时间"]
    lines.append("员工状态:")
    for r in rows:
        lines.append(f"- {r['name']}({r['role']}): {r['status']} · {r['cli']}")

    if busy:
        lines.append("进行中: " + "、".join(r["name"] for r in busy))
    else:
        lines.append("进行中: 暂无检测到 busy 的员工")

    if offline:
        lines.append("异常/离线: " + "、".join(r["name"] for r in offline))
    else:
        lines.append("异常/离线: 无")

    tasks = _unfinished_tasks()
    lines.append("任务进展:")
    if tasks:
        for t in tasks:
            lines.append(
                f"- {t.get('task_id', '?')} [{t.get('status', '?')}] "
                f"{t.get('assignee', '-')}: {t.get('title', '')}"
            )
    else:
        lines.append("- 当前本地任务单无未完成任务")

    return "\n".join(lines)


def say(message):
    return subprocess.run(
        ["python3", str(SCRIPTS / "feishu_msg.py"), "say", "manager", message],
        cwd=str(ROOT),
        text=True,
        capture_output=True,
        timeout=60,
    )


def run_once():
    report = build_report()
    result = say(report)
    if result.returncode != 0:
        sys.stderr.write(result.stderr or result.stdout)
    else:
        sys.stdout.write(result.stdout)
    return result.returncode


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--loop", action="store_true", help="run patrol forever")
    parser.add_argument("--interval", type=int, default=300, help="loop interval seconds")
    args = parser.parse_args()

    if not args.loop:
        raise SystemExit(run_once())

    while True:
        run_once()
        time.sleep(max(60, args.interval))


if __name__ == "__main__":
    main()
