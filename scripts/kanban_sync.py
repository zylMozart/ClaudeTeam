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
from config import load_runtime_config, save_runtime_config, LARK_CLI

TASKS_FILE = os.path.join(os.path.dirname(__file__), "..", "workspace", "shared", "tasks", "tasks.json")

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
    """拉取 Agent 状态表。

    返回
    ----
    dict  : {agent_name: {状态, 当前任务, 更新时间}}  查询成功(可能为空)
    None  : 查询失败 —— 调用方应跳过本轮同步,避免用空 dict 覆盖掉上一轮的
            真实状态 (ADR silent_swallow_remaining P0 ②)
    """
    bt = cfg["bitable_app_token"]
    st = cfg["sta_table_id"]
    d = _lark(["base", "+record-list", "--base-token", bt,
               "--table-id", st, "--limit", "100", "--as", "bot"],
              label="拉取状态表")
    if d is None:
        print("   🚨 状态表查询失败,本轮同步放弃以保留上一轮数据", file=sys.stderr)
        return None
    result = {}
    seen = set()
    for item in d.get("items", []):
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
    """获取看板表所有记录 ID。

    返回
    ----
    list[str] : record_id 列表(可能为空)
    None      : 查询失败 —— 调用方**必须**跳过 delete+create 整轮,否则
                空 list 会被当成"没东西可删"从而让旧记录残留,新记录叠加
                写入导致看板卡片重复 (ADR silent_swallow_remaining P0 ③)
    """
    bt, kt = cfg["bitable_app_token"], cfg["kanban_table_id"]
    d = _lark(["base", "+record-list", "--base-token", bt,
               "--table-id", kt, "--limit", "500", "--as", "bot"],
              label="获取看板记录")
    if d is None:
        return None
    return [item["record_id"] for item in d.get("items", [])]

BITABLE_BATCH_DELETE_LIMIT = 500  # Feishu bitable v1 batch_delete 单次上限


def delete_all_kanban_records(cfg):
    """删除看板表所有记录。

    返回
    ----
    True  : 所有批次都成功（或无记录可删）
    False : 任一批失败 —— 调用方应**跳过本轮写入**，保留旧看板状态等下一轮
            （宁要旧状态，不要新旧叠加）

    P1-7 修复: 原版逐条 `+record-delete`,500 条就是 500 次 API 调用,直接撞
    OpenAPIBatchAddRecords / record-delete 限流。改用通用 `lark-cli api POST
    /open-apis/bitable/v1/apps/{token}/tables/{tid}/records/batch_delete
    --data '{"records":[...]}' --as bot`,按 500/批分组。

    reviewer CR#2 (波次2 round1): 原 P1-7 patch 的 for 循环丢弃了 `_lark` 返回值,
    任一批失败会静默继续,do_sync 紧接着 bitable_batch_create 写新数据 →
    旧+新并存 → 看板卡片重复。现在返回 True/False 让上游决定。
    """
    ids = get_all_kanban_record_ids(cfg)
    # ADR silent_swallow_remaining P0 ③: None 表示查询失败,必须跳过整轮
    if ids is None:
        print("   🚨 获取看板记录列表失败,跳过本轮以保留旧数据", file=sys.stderr)
        return False
    if not ids:
        return True
    bt, kt = cfg["bitable_app_token"], cfg["kanban_table_id"]
    path = f"/open-apis/bitable/v1/apps/{bt}/tables/{kt}/records/batch_delete"

    for batch_start in range(0, len(ids), BITABLE_BATCH_DELETE_LIMIT):
        batch = ids[batch_start:batch_start + BITABLE_BATCH_DELETE_LIMIT]
        payload = json.dumps({"records": batch}, ensure_ascii=False)
        d = _lark(["api", "POST", path, "--data", payload, "--as", "bot"],
                  label=f"批删记录 {batch_start+1}-{batch_start+len(batch)}/{len(ids)}")
        if d is None:
            print(f"   🚨 批删记录失败 (batch {batch_start+1}-"
                  f"{batch_start+len(batch)}/{len(ids)}),跳过本轮 sync 写入,"
                  f"保留旧看板状态等下一轮")
            return False
    return True

def bitable_batch_create(cfg, records_json):
    """批量写入看板记录。

    返回
    ----
    True  : 写入成功
    False : 写入失败 —— 调用方应退出本轮剩余批次,下一轮 60s 后全量重刷
            (ADR silent_swallow_remaining P0 ①: 原版丢弃返回值导致部分写入
            失败时看板显示"✅ 看板已同步"但实际缺数据,用户看到看板一直少行)
    """
    bt, kt = cfg["bitable_app_token"], cfg["kanban_table_id"]
    d = _lark(["base", "+record-batch-create", "--base-token", bt,
               "--table-id", kt, "--json", records_json, "--as", "bot"],
              label="批量写入看板")
    if d is None:
        print("   🚨 看板批写失败,跳过本轮剩余批次,等下一轮重刷",
              file=sys.stderr)
        return False
    return True

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
    # ADR silent_swallow_remaining P0 ②: 状态查询失败 → None, 跳过本轮,
    # 避免用空 dict 覆盖掉上一轮的真实 agent 状态
    agent_status = fetch_all_agent_status(cfg)
    if agent_status is None:
        print("  ─ 跳过本轮(状态表查询失败)")
        return
    # reviewer CR#2 (波次2): delete 失败必须跳过写入,否则旧+新并存导致看板
    # 卡片重复。宁要上一轮的旧状态,不要一半一半的破损状态。
    if not delete_all_kanban_records(cfg):
        print("  ─ 跳过本轮看板写入(删除失败,保留旧状态)")
        return

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

    # ADR silent_swallow_remaining P0 ①: 任一批写入失败必须退出循环,不能
    # 继续写下一批(数据已经不一致了,再写只会让看板更错乱)。下一轮 60s
    # 后 do_sync 会重新全量 delete + create,自动修复。
    written = 0
    for batch in chunks(rows, 500):
        payload = json.dumps({"fields": field_names, "rows": batch}, ensure_ascii=False)
        if not bitable_batch_create(cfg, payload):
            print(f"  ─ 看板部分写入失败 (已写 {written}/{len(rows)}),"
                  f"等下一轮全量重刷")
            return
        written += len(batch)

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
