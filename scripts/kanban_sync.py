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
from claudeteam.runtime.config import load_runtime_config, save_runtime_config, LARK_CLI
from claudeteam.runtime.paths import legacy_script_state_file, runtime_state_file, runtime_state_dir, ensure_parent

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

BITABLE_BATCH_DELETE_LIMIT = 500  # Feishu bitable v1 batch_delete 单次上限

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

_PID_FILE = str(runtime_state_dir() / "kanban_sync.pid")
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
    ensure_parent(_PID_FILE)
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
