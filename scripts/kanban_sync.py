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
  Python 3.6+，requests，runtime_config.json（先运行 setup.py）
"""
import sys, os, json, time, requests
from datetime import datetime

sys.path.insert(0, os.path.dirname(__file__))
from config import APP_ID, APP_SECRET, BASE, CONFIG_FILE

TASKS_FILE = os.path.join(os.path.dirname(__file__), "..", "workspace", "shared", "tasks", "tasks.json")

# ── 基础工具 ──────────────────────────────────────────────────

from token_cache import get_token_cached

def get_token():
    return get_token_cached(APP_ID, APP_SECRET, BASE)

def h(token):
    return {"Authorization": f"Bearer {token}", "Content-Type": "application/json"}

def load_cfg():
    if not os.path.exists(CONFIG_FILE):
        print("❌ 未找到 runtime_config.json，请先运行 python3 scripts/setup.py")
        sys.exit(1)
    with open(CONFIG_FILE) as f:
        return json.load(f)

def save_cfg(cfg):
    with open(CONFIG_FILE, "w") as f:
        json.dump(cfg, f, indent=2, ensure_ascii=False)

def load_tasks():
    if not os.path.exists(TASKS_FILE):
        return {"tasks": []}
    with open(TASKS_FILE, encoding="utf-8") as f:
        return json.load(f)

def txt(v):
    if isinstance(v, list): return v[0].get("text", "") if v else ""
    return str(v) if v else ""

def to_ms(iso_str):
    """ISO 8601 字符串 → Unix 毫秒时间戳，解析失败返回 0。"""
    try:
        return int(datetime.fromisoformat(iso_str).timestamp() * 1000)
    except Exception:
        return 0

def chunks(lst, n):
    for i in range(0, len(lst), n):
        yield lst[i:i+n]

# ── Bitable 操作 ──────────────────────────────────────────────

def fetch_all_agent_status(token, cfg):
    """拉取 Agent 状态表，返回 {agent_name: {状态, 当前任务, 更新时间}} 字典。"""
    bt = cfg["bitable_app_token"]
    st = cfg["sta_table_id"]
    r = requests.post(
        f"{BASE}/bitable/v1/apps/{bt}/tables/{st}/records/search",
        headers=h(token),
        json={"page_size": 100, "sort": [{"field_name": "更新时间", "desc": True}]}
    )
    result = {}
    seen = set()
    for item in r.json().get("data", {}).get("items", []):
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

def get_all_kanban_record_ids(token, cfg):
    """分页获取看板表所有记录 ID。"""
    bt, kt = cfg["bitable_app_token"], cfg["kanban_table_id"]
    ids = []
    page_token = None
    while True:
        body = {"page_size": 500}
        if page_token:
            body["page_token"] = page_token
        d = requests.post(
            f"{BASE}/bitable/v1/apps/{bt}/tables/{kt}/records/search",
            headers=h(token), json=body
        ).json().get("data", {})
        ids.extend(item["record_id"] for item in d.get("items", []))
        if not d.get("has_more"):
            break
        page_token = d.get("page_token")
    return ids

def delete_all_kanban_records(token, cfg):
    """全量删除看板表现有记录（全量重建前清空）。"""
    ids = get_all_kanban_record_ids(token, cfg)
    if not ids:
        return
    bt, kt = cfg["bitable_app_token"], cfg["kanban_table_id"]
    for batch in chunks(ids, 500):
        requests.post(
            f"{BASE}/bitable/v1/apps/{bt}/tables/{kt}/records/batch_delete",
            headers=h(token), json={"records": batch}
        )

def bitable_batch_create(token, cfg, records):
    """批量写入看板记录，失败时打印警告。"""
    bt, kt = cfg["bitable_app_token"], cfg["kanban_table_id"]
    r = requests.post(
        f"{BASE}/bitable/v1/apps/{bt}/tables/{kt}/records/batch_create",
        headers=h(token), json={"records": records}
    )
    if r.json().get("code") != 0:
        print(f"⚠️  批量写入失败: {r.json()}")

# ── 命令：init ────────────────────────────────────────────────

def cmd_init():
    """在现有 Bitable 中创建"项目看板"表，将 kanban_table_id 写入 runtime_config.json。"""
    token = get_token()
    cfg = load_cfg()

    if cfg.get("kanban_table_id"):
        print(f"⚠️  项目看板表已存在: {cfg['kanban_table_id']}，跳过创建")
        return

    bt = cfg["bitable_app_token"]
    r = requests.post(f"{BASE}/bitable/v1/apps/{bt}/tables",
        headers=h(token), json={"table": {"name": "项目看板", "fields": [
            {"field_name": "任务ID",        "type": 1},
            {"field_name": "标题",          "type": 1},
            {"field_name": "状态",          "type": 3, "property": {"options": [
                {"name": "待处理", "color": 4}, {"name": "进行中", "color": 1},
                {"name": "已完成", "color": 2}, {"name": "已取消", "color": 5},
            ]}},
            {"field_name": "负责人",        "type": 1},
            {"field_name": "Agent当前状态", "type": 1},
            {"field_name": "Agent当前任务", "type": 1},
            {"field_name": "任务更新时间",  "type": 5},
            {"field_name": "Agent状态更新", "type": 5},
        ]}})
    d = r.json()
    if d.get("code") != 0:
        print(f"❌ 创建项目看板表失败: {d}"); sys.exit(1)

    table_id = d["data"]["table_id"]
    cfg["kanban_table_id"] = table_id
    save_cfg(cfg)
    print(f"✅ 项目看板表已创建: {table_id}")
    print(f"   配置已更新: runtime_config.json → kanban_table_id")

# ── 命令：sync ────────────────────────────────────────────────

def do_sync(token, cfg):
    """全量重建看板表：读取本地任务 + Agent 状态 → 清空旧数据 → 写入新快照。"""
    tasks = load_tasks().get("tasks", [])
    agent_status = fetch_all_agent_status(token, cfg)
    delete_all_kanban_records(token, cfg)

    records = []
    for task in tasks:
        assignee = task["assignee"]
        ast = agent_status.get(assignee, {})
        record = {"fields": {
            "任务ID":        task["task_id"],
            "标题":          task["title"],
            "状态":          task["status"],
            "负责人":        assignee,
            "Agent当前状态": ast.get("状态", "未知"),
            "Agent当前任务": ast.get("当前任务", ""),
        }}
        # 日期字段仅在有值时写入，避免显示为1970年
        t_task = to_ms(task.get("updated_at", ""))
        if t_task:
            record["fields"]["任务更新时间"] = t_task
        t_agent = ast.get("更新时间", 0)
        if t_agent:
            record["fields"]["Agent状态更新"] = t_agent
        records.append(record)

    for batch in chunks(records, 500):
        bitable_batch_create(token, cfg, batch)

    print(f"✅ 看板已同步: {len(records)} 条任务")

def cmd_sync():
    token = get_token()
    cfg = load_cfg()
    if not cfg.get("kanban_table_id"):
        print("❌ 未找到 kanban_table_id，请先运行: python3 scripts/kanban_sync.py init")
        sys.exit(1)
    do_sync(token, cfg)

# ── 命令：daemon ──────────────────────────────────────────────

def cmd_daemon(interval=60):
    print(f"🔄 看板同步守护进程启动（每 {interval} 秒同步一次）")
    while True:
        try:
            token = get_token()
            cfg = load_cfg()
            if cfg.get("kanban_table_id"):
                do_sync(token, cfg)
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
