#!/usr/bin/env python3
"""Manager patrol reporter.

Reports only when there is a real active task in progress.
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


def _recent_completed_tasks(all_tasks, limit=3):
    tasks = [
        t for t in all_tasks
        if t.get("status") in ("已完成", "完成", "done", "completed")
    ]
    tasks.sort(key=lambda t: t.get("updated_at", ""), reverse=True)
    return tasks[:limit]


def _load_tasks():
    if not TASKS_FILE.is_file():
        return []
    try:
        data = json.loads(TASKS_FILE.read_text(encoding="utf-8"))
    except Exception:
        return []
    return data.get("tasks", [])


def _active_task_context(rows, tasks):
    """Return report context only when a patrol is justified.

    Product rule: a 5-minute patrol is allowed only when a real task is in
    progress and at least one assigned worker is currently busy. If either
    signal is missing, stay silent rather than sending a misleading idle list.
    """
    busy_by_name = {r["name"]: r for r in rows if r.get("status") == "busy"}
    active_tasks = [
        t for t in tasks
        if t.get("status") in ("进行中", "in_progress", "running")
    ]
    active_with_busy_worker = [
        t for t in active_tasks
        if t.get("assignee") in busy_by_name
    ]
    if not active_with_busy_worker:
        return None
    blocked = [
        t for t in tasks
        if t.get("status") in ("阻塞", "blocked")
    ]
    return {
        "busy": busy_by_name,
        "active_tasks": active_with_busy_worker,
        "blocked_tasks": blocked,
        "completed_tasks": _recent_completed_tasks(tasks),
    }


def build_report(rows=None, tasks=None):
    now = datetime.now(BJ_TZ).strftime("%Y-%m-%d %H:%M")
    rows = rows if rows is not None else collect_team_status()
    tasks = tasks if tasks is not None else _load_tasks()
    ctx = _active_task_context(rows, tasks)
    if ctx is None:
        return None

    lines = [f"5分钟巡视 · {now} 北京时间"]
    lines.append(
        f"项目阶段: 执行中（{len(ctx['active_tasks'])} 个任务正在推进）"
    )

    lines.append("已完成:")
    if ctx["completed_tasks"]:
        for t in ctx["completed_tasks"]:
            lines.append(f"- {t.get('task_id', '?')}: {t.get('title', '')}")
    else:
        lines.append("- 本轮未发现新完成项")

    lines.append("当前卡点:")
    if ctx["blocked_tasks"]:
        for t in ctx["blocked_tasks"][:3]:
            blocker = t.get("blocker") or t.get("description") or "未记录原因"
            lines.append(
                f"- {t.get('task_id', '?')} {t.get('assignee', '-')}: {blocker}"
            )
    else:
        lines.append("- 暂无明确卡点")

    lines.append("下一步推进:")
    for t in ctx["active_tasks"]:
        assignee = t.get("assignee", "-")
        worker = ctx["busy"].get(assignee, {})
        lines.append(
            f"- {t.get('task_id', '?')} {assignee}: 继续推进"
            f"「{t.get('title', '')}」"
            f"（当前执行环境: {worker.get('cli', 'unknown')} busy）"
        )
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
    if not report:
        sys.stdout.write("manager_patrol: no active task context; skip report\n")
        return 0
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
