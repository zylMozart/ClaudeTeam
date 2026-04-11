#!/usr/bin/env python3
"""
项目看板同步 — ClaudeTeam

功能描述:
  聚合 task_tracker 任务数据 + Bitable Agent状态 → 写入飞书项目看板表（全量快照刷新）

输入输出:
  CLI 子命令:
    init                    — 在现有 Bitable 中创建"项目看板"表
    sync                    — 执行一次全量同步
    daemon [--interval N]   — 后台定时同步（默认60秒一次）

依赖:
  Python 3.6+, lark-cli, runtime_config.json（先运行 setup.py）
"""
import sys, os, json, time, subprocess, atexit, signal
from datetime import datetime

sys.path.insert(0, os.path.dirname(__file__))
from config import load_runtime_config, save_runtime_config

TASKS_FILE = os.path.join(os.path.dirname(__file__), "..", "workspace", "shared", "tasks", "tasks.json")
LARK_CLI = ["npx", "@larksuite/cli"]

# ── 基础工具 ──────────────────────────────────────────────────

load_cfg = load_runtime_config
save_cfg = save_runtime_config

def _lark(args, label="", timeout=30):
    r = subprocess.run(LARK_CLI + args, capture_output=True, text=True, timeout=timeout)
    if r.returncode != 0:
        print(f"   ⚠️ {label}: {r.stderr.strip()[:200]}")
        return None
    try:
        full = json.loads(r.stdout) if r.stdout.strip() else {}
        return full.get("data", full)
    except json.JSONDecodeError:
        return None

def load_tasks():
    if not os.path.exists(TASKS_FILE):
        return {"tasks": []}
    with open(TASKS_FILE, encoding="utf-8") as f:
        return json.load(f)

def extract_text(v):
    if isinstance(v, list): return v[0].get("text", "") if v else ""
    return str(v) if v else ""

txt = extract_text

def to_ms(iso_str):
    try:
        return int(datetime.fromisoformat(iso_str).timestamp() * 1000)
    except Exception:
        return 0

def chunks(lst, n):
    for i in range(0, len(lst), n):
        yield lst[i:i+n]

# ── Bitable 操作（lark-cli）──────────────────────────────────

def fetch_all_agent_status(cfg):
    """拉取 Agent 状态表。"""
    bt = cfg["bitable_app_token"]
    st = cfg["sta_table_id"]
    d = _lark(["base", "+record-list", "--base-token", bt,
               "--table-id", st, "--limit", "100", "--as", "bot"],
              label="拉取状态表")
    result = {}
    seen = set()
    for item in (d or {}).get("data", {}).get("items", []):
        f = item.get("fields", {})
        agent = txt(f.get("Agent名称", ""))
        if agent and agent not in seen:
            seen.add(agent)
            updated_ms = f.get("更新时间", 0)
            if isinstance(updated_ms, list):
                updated_ms = updated_ms[0].get("value", 0) if updated_ms else 0
            result[agent] = {
                "状态":     txt(f.get("状态", "")),
                "当前任务": txt(f.get("当前任务", "")),
                "更新时间": updated_ms,
            }
    return result

def get_all_kanban_record_ids(cfg):
    """获取看板表所有记录 ID。"""
    bt, kt = cfg["bitable_app_token"], cfg["kanban_table_id"]
    d = _lark(["base", "+record-list", "--base-token", bt,
               "--table-id", kt, "--limit", "500", "--as", "bot"],
              label="获取看板记录")
    return [item["record_id"] for item in (d or {}).get("data", {}).get("items", [])]

def delete_all_kanban_records(cfg):
    """删除看板表所有记录。"""
    ids = get_all_kanban_record_ids(cfg)
    if not ids:
        return
    bt, kt = cfg["bitable_app_token"], cfg["kanban_table_id"]
    for rid in ids:
        _lark(["base", "+record-delete", "--base-token", bt,
               "--table-id", kt, "--record-id", rid, "--yes", "--as", "bot"],
              label=f"删除记录 {rid[:8]}")

def bitable_batch_create(cfg, records_json):
    """批量写入看板记录。"""
    bt, kt = cfg["bitable_app_token"], cfg["kanban_table_id"]
    _lark(["base", "+record-batch-create", "--base-token", bt,
           "--table-id", kt, "--json", records_json, "--as", "bot"],
          label="批量写入看板")

# ── 命令：init ────────────────────────────────────────────────

def cmd_init():
    cfg = load_cfg()

    if cfg.get("kanban_table_id"):
        print(f"⚠️  项目看板表已存在: {cfg['kanban_table_id']}，跳过创建")
        return

    bt = cfg["bitable_app_token"]
    fields = json.dumps([
        {"name": "任务ID", "type": "text"},
        {"name": "标题", "type": "text"},
        {"name": "状态", "type": "text"},
        {"name": "负责人", "type": "text"},
        {"name": "Agent当前状态", "type": "text"},
        {"name": "Agent当前任务", "type": "text"},
        {"name": "任务更新时间", "type": "date_time"},
        {"name": "Agent状态更新", "type": "date_time"},
    ], ensure_ascii=False)
    d = _lark(["base", "+table-create", "--base-token", bt,
               "--name", "项目看板", "--fields", fields, "--as", "bot"],
              label="创建看板表")
    # +table-create 返回 data.table.id
    tid = ""
    if d:
        if isinstance(d.get("table"), dict):
            tid = d["table"].get("id", d["table"].get("table_id", ""))
        else:
            tid = d.get("table_id", "")
    if not tid:
        print(f"❌ 创建项目看板表失败: {d}"); sys.exit(1)

    cfg["kanban_table_id"] = tid
    save_cfg(cfg)
    print(f"✅ 项目看板表已创建: {tid}")

# ── 命令：sync ────────────────────────────────────────────────

def do_sync(cfg):
    tasks = load_tasks().get("tasks", [])
    agent_status = fetch_all_agent_status(cfg)
    delete_all_kanban_records(cfg)

    if not tasks:
        print("  ─ 无任务记录")
        return

    field_names = ["任务ID", "标题", "状态", "负责人", "Agent当前状态", "Agent当前任务"]
    rows = []
    for task in tasks:
        assignee = task["assignee"]
        ast = agent_status.get(assignee, {})
        rows.append([
            task["task_id"],
            task["title"],
            task["status"],
            assignee,
            ast.get("状态", "未知"),
            ast.get("当前任务", ""),
        ])

    for batch in chunks(rows, 500):
        payload = json.dumps({"fields": field_names, "rows": batch}, ensure_ascii=False)
        bitable_batch_create(cfg, payload)

    print(f"✅ 看板已同步: {len(rows)} 条任务")

def cmd_sync():
    cfg = load_cfg()
    if not cfg.get("kanban_table_id"):
        print("❌ 未找到 kanban_table_id，请先运行: python3 scripts/kanban_sync.py init")
        sys.exit(1)
    do_sync(cfg)

# ── 命令：daemon ──────────────────────────────────────────────

_PID_FILE = os.path.join(os.path.dirname(__file__), ".kanban_sync.pid")

def _acquire_pid_lock():
    if os.path.exists(_PID_FILE):
        try:
            with open(_PID_FILE) as f:
                old_pid = int(f.read().strip())
            os.kill(old_pid, 0)
            print(f"❌ kanban_sync daemon 已在运行 (PID {old_pid})")
            sys.exit(1)
        except (ValueError, OSError):
            pass
    with open(_PID_FILE, "w") as f:
        f.write(str(os.getpid()))
    atexit.register(_cleanup_pid)

def _cleanup_pid():
    try:
        if os.path.exists(_PID_FILE):
            with open(_PID_FILE) as f:
                pid = int(f.read().strip())
            if pid == os.getpid():
                os.remove(_PID_FILE)
    except Exception:
        pass

signal.signal(signal.SIGTERM, lambda *_: sys.exit(0))

def cmd_daemon(interval=60):
    _acquire_pid_lock()
    print(f"🔄 看板同步守护进程启动（每 {interval} 秒同步一次）")
    while True:
        try:
            cfg = load_cfg()
            if cfg.get("kanban_table_id"):
                do_sync(cfg)
            else:
                print("⚠️  kanban_table_id 未配置，跳过本次同步")
        except Exception as e:
            print(f"⚠️  同步失败: {e}")
        time.sleep(interval)

# ── main ──────────────────────────────────────────────────────

def main():
    args = sys.argv[1:]
    if not args:
        print(__doc__); sys.exit(0)

    cmd = args[0]

    if cmd == "init":
        cmd_init()
    elif cmd == "sync":
        cmd_sync()
    elif cmd == "daemon":
        interval = 60
        if "--interval" in args:
            idx = args.index("--interval")
            if idx + 1 < len(args):
                interval = int(args[idx + 1])
        cmd_daemon(interval)
    else:
        print(f"未知命令: {cmd}"); sys.exit(1)

if __name__ == "__main__":
    main()
