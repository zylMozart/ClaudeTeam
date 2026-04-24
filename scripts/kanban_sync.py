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

_SCRIPT_DIR = os.path.dirname(__file__)
sys.path.insert(0, _SCRIPT_DIR)
_SRC_DIR = os.path.join(os.path.dirname(_SCRIPT_DIR), "src")
if _SRC_DIR not in sys.path:
    sys.path.insert(0, _SRC_DIR)

from claudeteam.commands import kanban_sync as _kanban_commands
from claudeteam.commands import kanban_daemon as _kanban_daemon
from claudeteam.integrations.feishu import kanban_projection as _kanban_projection
from claudeteam.integrations.feishu import kanban_service as _kanban_service
from config import load_runtime_config, save_runtime_config, LARK_CLI
from claudeteam.runtime.paths import legacy_script_state_file, runtime_state_file

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
    return _kanban_projection.extract_text(v)

txt = extract_text

def to_ms(iso_str):
    return _kanban_projection.to_ms(iso_str)

def chunks(lst, n):
    return _kanban_projection.chunks(lst, n)

# ── Bitable 操作（lark-cli）──────────────────────────────────

def fetch_all_agent_status(cfg):
    """拉取 Agent 状态表。

    返回
    ----
    dict  : {agent_name: {状态, 当前任务, 更新时间}}  查询成功(可能为空)
    None  : 查询失败 —— 调用方应跳过本轮同步,避免用空 dict 覆盖掉上一轮的
            真实状态 (ADR silent_swallow_remaining P0 ②)
    """
    return _kanban_service.fetch_all_agent_status_with_run(cfg, _lark)

def get_all_kanban_record_ids(cfg):
    """获取看板表所有记录 ID。

    返回
    ----
    list[str] : record_id 列表(可能为空)
    None      : 查询失败 —— 调用方**必须**跳过 delete+create 整轮,否则
                空 list 会被当成"没东西可删"从而让旧记录残留,新记录叠加
                写入导致看板卡片重复 (ADR silent_swallow_remaining P0 ③)
    """
    return _kanban_service.get_all_kanban_record_ids_with_run(cfg, _lark)

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
    return _kanban_service.delete_all_kanban_records_with_run(
        cfg,
        _lark,
        batch_delete_limit=BITABLE_BATCH_DELETE_LIMIT,
    )

def bitable_batch_create(cfg, records_json):
    """批量写入看板记录。

    返回
    ----
    True  : 写入成功
    False : 写入失败 —— 调用方应退出本轮剩余批次,下一轮 60s 后全量重刷
            (ADR silent_swallow_remaining P0 ①: 原版丢弃返回值导致部分写入
            失败时看板显示"✅ 看板已同步"但实际缺数据,用户看到看板一直少行)
    """
    return _kanban_service.bitable_batch_create_with_run(cfg, records_json, _lark)

# ── 命令：init ────────────────────────────────────────────────

def cmd_init():
    cfg = load_cfg()
    ok, payload = _kanban_service.ensure_kanban_table_with_run(cfg, _lark, save_cfg)
    if not ok:
        print(f"❌ 创建项目看板表失败: {payload}")
        sys.exit(1)

# ── 命令：sync ────────────────────────────────────────────────

def do_sync(cfg):
    tasks = load_tasks().get("tasks", [])
    return _kanban_service.sync_kanban_snapshot_with_run(
        cfg,
        tasks,
        _lark,
        batch_delete_limit=BITABLE_BATCH_DELETE_LIMIT,
        batch_size=500,
    )

def cmd_sync():
    cfg = load_cfg()
    if not cfg.get("kanban_table_id"):
        print("❌ 未找到 kanban_table_id，请先运行: python3 scripts/kanban_sync.py init")
        sys.exit(1)
    do_sync(cfg)

# ── 命令：daemon ──────────────────────────────────────────────

_PID_FILE = runtime_state_file("kanban_sync.pid")
_LEGACY_PID_FILE = legacy_script_state_file(".kanban_sync.pid")


def _pid_file_is_live_kanban(path):
    def _read_text(file_path):
        with open(file_path) as f:
            return f.read()

    def _pid_is_alive(pid):
        os.kill(pid, 0)
        return True

    def _read_cmdline(pid):
        with open(f"/proc/{pid}/cmdline", "rb") as f:
            return f.read().decode("utf-8", errors="ignore")

    return _kanban_daemon.pid_file_is_live(
        path,
        path_exists=os.path.exists,
        read_text=_read_text,
        pid_is_alive=_pid_is_alive,
        read_cmdline=_read_cmdline,
        expected_fragment="kanban_sync.py",
    )

def _acquire_pid_lock():
    for path in (_PID_FILE, _LEGACY_PID_FILE):
        if _pid_file_is_live_kanban(path):
            with open(path) as f:
                old_pid = int(f.read().strip())
            print(f"❌ kanban_sync daemon 已在运行 (PID {old_pid})")
            sys.exit(1)
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
        print(__doc__)
        sys.exit(0)

    handlers = {
        "init": cmd_init,
        "sync": cmd_sync,
        "daemon": cmd_daemon,
    }
    result = _kanban_commands.run(args, handlers=handlers)
    if result.exit_code != 0:
        if result.message:
            print(result.message)
        sys.exit(result.exit_code)
    if not result.handled and result.message:
        print(result.message)

if __name__ == "__main__":
    main()
