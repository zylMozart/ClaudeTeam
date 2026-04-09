#!/usr/bin/env python3
"""
任务单系统 — ClaudeTeam

功能描述:
  本地 JSON 文件存储任务单，支持多 Agent 协作时追踪任务状态。
  存储路径: workspace/shared/tasks/tasks.json

输入输出:
  CLI 子命令:
    create <assignee> "<title>" ["<description>"] [--by <creator>]
    update <task_id> [--status <状态>] [--assignee <agent>] [--title "<新标题>"]
    list   [--status <状态>] [--assignee <agent>]
    get    <task_id>

依赖:
  Python 3.6+，仅用标准库
"""
import sys, os, json
from datetime import datetime, timezone, timedelta

TASKS_FILE = os.path.join(os.path.dirname(__file__), "..", "workspace", "shared", "tasks", "tasks.json")

VALID_STATUSES = {"待处理", "进行中", "已完成", "已取消"}

# ── 文件读写（原子性保证）─────────────────────────────────────────

def load_tasks():
    """读取 tasks.json，若不存在则返回空结构。"""
    if not os.path.exists(TASKS_FILE):
        return {"tasks": [], "_meta": {"last_id": 0}}
    try:
        with open(TASKS_FILE, "r", encoding="utf-8") as f:
            return json.load(f)
    except json.JSONDecodeError as e:
        print(f"❌ tasks.json 解析失败: {e}")
        sys.exit(1)

def save_tasks(data):
    """原子写入：先写 .tmp 再 os.replace，防止写一半损坏文件。"""
    os.makedirs(os.path.dirname(TASKS_FILE), exist_ok=True)
    tmp = TASKS_FILE + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)
    os.replace(tmp, TASKS_FILE)

# ── task_id 自增生成 ───────────────────────────────────────────

def next_task_id(data):
    n = data["_meta"]["last_id"] + 1
    data["_meta"]["last_id"] = n
    return f"TASK-{n:03d}"

def now_iso():
    return datetime.now(timezone(timedelta(hours=8))).isoformat()

# ── 命令：create ───────────────────────────────────────────────

def cmd_create(assignee, title, description="", created_by=""):
    """
    创建新任务，自动分配 TASK-NNN 编号。

    Args:
        assignee:    负责人 Agent 名称
        title:       任务标题
        description: 任务描述（可选）
        created_by:  创建人 Agent 名称（可选）
    """
    data = load_tasks()
    task_id = next_task_id(data)
    now = now_iso()
    task = {
        "task_id":     task_id,
        "title":       title,
        "status":      "待处理",
        "assignee":    assignee,
        "created_at":  now,
        "updated_at":  now,
        "created_by":  created_by,
        "description": description,
    }
    data["tasks"].append(task)
    save_tasks(data)
    print(f"✅ 任务已创建: {task_id} [assignee: {assignee}] {title}")
    return task_id

# ── 命令：update ───────────────────────────────────────────────

def cmd_update(task_id, status=None, assignee=None, title=None):
    """
    更新任务的状态、指派人或标题，自动刷新 updated_at。

    Args:
        task_id:  要更新的任务 ID（如 TASK-001）
        status:   新状态（可选）
        assignee: 新负责人（可选）
        title:    新标题（可选）
    """
    data = load_tasks()
    task = next((t for t in data["tasks"] if t["task_id"] == task_id), None)
    if not task:
        print(f"❌ 找不到任务: {task_id}")
        sys.exit(1)

    if status is not None:
        if status not in VALID_STATUSES:
            print(f"⚠️  状态 '{status}' 不在标准枚举中，已强制写入")
        task["status"] = status
    if assignee is not None:
        task["assignee"] = assignee
    if title is not None:
        task["title"] = title

    task["updated_at"] = now_iso()
    save_tasks(data)
    print(f"✅ 任务已更新: {task_id}")

# ── 命令：list ─────────────────────────────────────────────────

def cmd_list(filter_status=None, filter_assignee=None):
    """
    列出所有任务，支持按状态和负责人过滤。

    Args:
        filter_status:   只显示此状态的任务（可选）
        filter_assignee: 只显示此负责人的任务（可选）
    """
    data = load_tasks()
    tasks = data["tasks"]

    if filter_status:
        tasks = [t for t in tasks if t["status"] == filter_status]
    if filter_assignee:
        tasks = [t for t in tasks if t["assignee"] == filter_assignee]

    print(f"📋 任务列表 (共{len(tasks)}条):\n")
    for t in tasks:
        print(f"  {t['task_id']:<10} [{t['status']:<4}]  {t['assignee']:<12} {t['title']}")

# ── 命令：get ──────────────────────────────────────────────────

def cmd_get(task_id):
    """
    获取单条任务的详细信息。

    Args:
        task_id: 任务 ID（如 TASK-001）
    """
    data = load_tasks()
    task = next((t for t in data["tasks"] if t["task_id"] == task_id), None)
    if not task:
        print(f"❌ 找不到任务: {task_id}")
        sys.exit(1)

    # 格式化时间：去掉秒以下和时区，更简洁
    def fmt_time(iso_str):
        try:
            return iso_str[:16].replace("T", " ")
        except Exception:
            return iso_str

    desc = task.get("description") or "（无）"
    print(f"── {task['task_id']} ──────────────────────────────")
    print(f"  标题:    {task['title']}")
    print(f"  状态:    {task['status']}")
    print(f"  负责人:  {task['assignee']}")
    print(f"  创建人:  {task.get('created_by') or '（无）'}")
    print(f"  创建时间: {fmt_time(task['created_at'])}")
    print(f"  更新时间: {fmt_time(task['updated_at'])}")
    print(f"  描述:    {desc}")

# ── main ──────────────────────────────────────────────────────

def main():
    args = sys.argv[1:]
    if not args:
        print(__doc__)
        sys.exit(0)

    cmd = args[0]

    if cmd == "create":
        # create <assignee> "<title>" ["<description>"] [--by <creator>]
        if len(args) < 3:
            print("用法: create <assignee> \"<title>\" [\"<description>\"] [--by <creator>]")
            sys.exit(1)
        assignee = args[1]
        title = args[2]
        description = ""
        created_by = ""

        # 解析可选的 --by 和 description
        rest = args[3:]
        i = 0
        while i < len(rest):
            if rest[i] == "--by" and i + 1 < len(rest):
                created_by = rest[i + 1]
                i += 2
            else:
                if not description:
                    description = rest[i]
                i += 1

        cmd_create(assignee, title, description, created_by)

    elif cmd == "update":
        # update <task_id> [--status <状态>] [--assignee <agent>] [--title "<新标题>"]
        if len(args) < 2:
            print("用法: update <task_id> [--status <状态>] [--assignee <agent>] [--title \"<新标题>\"]")
            sys.exit(1)
        task_id = args[1]
        status = assignee = title = None

        rest = args[2:]
        i = 0
        while i < len(rest):
            if rest[i] == "--status" and i + 1 < len(rest):
                status = rest[i + 1]; i += 2
            elif rest[i] == "--assignee" and i + 1 < len(rest):
                assignee = rest[i + 1]; i += 2
            elif rest[i] == "--title" and i + 1 < len(rest):
                title = rest[i + 1]; i += 2
            else:
                i += 1

        cmd_update(task_id, status, assignee, title)

    elif cmd == "list":
        # list [--status <状态>] [--assignee <agent>]
        filter_status = filter_assignee = None
        rest = args[1:]
        i = 0
        while i < len(rest):
            if rest[i] == "--status" and i + 1 < len(rest):
                filter_status = rest[i + 1]; i += 2
            elif rest[i] == "--assignee" and i + 1 < len(rest):
                filter_assignee = rest[i + 1]; i += 2
            else:
                i += 1
        cmd_list(filter_status, filter_assignee)

    elif cmd == "get":
        if len(args) < 2:
            print("用法: get <task_id>")
            sys.exit(1)
        cmd_get(args[1])

    else:
        print(f"未知命令: {cmd}")
        sys.exit(1)

if __name__ == "__main__":
    main()
