#!/usr/bin/env python3
"""
Watchdog Daemon — 监控并自动重启关键守护进程
运行：python3 scripts/watchdog.py

监控对象:
  router (lark-cli event | feishu_router.py) — 消息路由（核心依赖）
  kanban_sync.py — 看板同步守护进程

检测方式: PID 锁文件 / pgrep，60秒检查一次
重启方式: subprocess.Popen start_new_session=True
通知方式: 子进程调用 feishu_msg.py send
"""
import sys, os, time, subprocess, atexit, signal

sys.path.insert(0, os.path.dirname(__file__))
from config import PROJECT_ROOT

CHECK_INTERVAL = 60  # 秒

PROCS = [
    {
        "name":  "router (lark-cli event | router)",
        "match": "feishu_router.py",
        "cmd":   ["bash", "-c",
                  "npx @larksuite/cli event +subscribe "
                  "--event-types im.message.receive_v1 "
                  "--compact --quiet --force "
                  "| python3 scripts/feishu_router.py --stdin"],
        "pid_file": os.path.join(os.path.dirname(__file__), ".router.pid"),
        "max_retries": 3,
        "retry_count": 0,
    },
    {
        "name":  "kanban_sync.py",
        "match": "kanban_sync.py daemon",
        "cmd":   ["python3", "scripts/kanban_sync.py", "daemon"],
        "pid_file": os.path.join(os.path.dirname(__file__), ".kanban_sync.pid"),
        "max_retries": 3,
        "retry_count": 0,
    },
]

def log(msg):
    print(f"[{time.strftime('%Y-%m-%d %H:%M:%S')}] {msg}", flush=True)

def is_running_by_pid_file(pid_file):
    """通过 PID 锁文件检测进程是否存活（精确匹配本项目）"""
    if not os.path.exists(pid_file):
        return False
    try:
        with open(pid_file) as f:
            pid = int(f.read().strip())
        os.kill(pid, 0)
        return True
    except (ValueError, OSError):
        return False

def is_running(match_str):
    """降级方案：pgrep 匹配（用于没有 PID 文件的进程）"""
    r = subprocess.run(["pgrep", "-f", match_str], capture_output=True)
    return r.returncode == 0

def restart_process(proc):
    subprocess.Popen(
        proc["cmd"],
        cwd=PROJECT_ROOT,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
        start_new_session=True,
    )
    log(f"🔄 已重启: {proc['name']}")

def notify_manager(proc_name):
    msg = f"[watchdog] {proc_name} 已崩溃并自动重启，请确认运行状态。"
    subprocess.run(
        ["python3", "scripts/feishu_msg.py",
         "send", "manager", "watchdog", msg, "高"],
        cwd=PROJECT_ROOT,
        capture_output=True,
    )
    log(f"📨 已通知 manager: {proc_name} 重启")

def is_healthy(proc):
    """进程存在 + 健康检查通过"""
    pid_file = proc.get("pid_file")
    if pid_file:
        if not is_running_by_pid_file(pid_file):
            return False
    elif not is_running(proc["match"]):
        return False
    health_file = proc.get("health_file")
    if health_file and os.path.exists(health_file):
        age = time.time() - os.path.getmtime(health_file)
        if age > proc.get("health_stale_secs", 300):
            log(f"⚠️ {proc['name']} 健康检查失败：输出文件 {age:.0f}s 未更新")
            return False
    return True

def check_once():
    all_ok = True
    for proc in PROCS:
        if not is_healthy(proc):
            all_ok = False
            proc["retry_count"] = proc.get("retry_count", 0) + 1
            max_retries = proc.get("max_retries", 3)
            if proc["retry_count"] > max_retries:
                log(f"🚨 {proc['name']} 连续 {max_retries} 次重启失败，停止重试")
                notify_manager(f"{proc['name']} 连续 {max_retries} 次重启失败，需人工介入")
                continue
            log(f"💀 检测到异常: {proc['name']} (第 {proc['retry_count']} 次)")
            restart_process(proc)
            time.sleep(2)
            notify_manager(proc["name"])
        else:
            if proc.get("retry_count", 0) > 0:
                proc["retry_count"] = 0  # 恢复正常，重置计数
    if all_ok:
        log("✅ 所有守护进程运行正常")

_PID_FILE = os.path.join(os.path.dirname(__file__), ".watchdog.pid")

def _acquire_pid_lock():
    if os.path.exists(_PID_FILE):
        try:
            with open(_PID_FILE) as f:
                old_pid = int(f.read().strip())
            os.kill(old_pid, 0)
            log(f"❌ Watchdog 已在运行 (PID {old_pid})")
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

def main():
    _acquire_pid_lock()
    log("🐕 Watchdog 启动")
    log(f"   监控对象: {', '.join(p['name'] for p in PROCS)}")
    log(f"   检查间隔: {CHECK_INTERVAL}s")
    log("=" * 55)

    while True:
        try:
            check_once()
        except Exception as e:
            log(f"⚠️  Watchdog 异常: {e}")
        time.sleep(CHECK_INTERVAL)

if __name__ == "__main__":
    main()
