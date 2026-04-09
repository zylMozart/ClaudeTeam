#!/usr/bin/env python3
"""
Watchdog Daemon — 监控并自动重启关键守护进程
运行：python3 scripts/watchdog.py

监控对象:
  feishu_router.py — 消息路由（核心依赖）
  kanban_sync.py   — 看板同步守护进程

检测方式: pgrep -f <脚本名>，60秒轮询一次
重启方式: subprocess.Popen start_new_session=True
通知方式: 子进程调用 feishu_msg.py send
"""
import sys, os, time, subprocess

sys.path.insert(0, os.path.dirname(__file__))
from config import PROJECT_ROOT

CHECK_INTERVAL = 60  # 秒

PROCS = [
    {
        "name":  "feishu_router.py",
        "match": "feishu_router.py",
        "cmd":   ["python3", "scripts/feishu_router.py"],
        "health_file": os.path.join(os.path.dirname(__file__), ".router_seen_ids.json"),
        "health_stale_secs": 300,  # 5 分钟无更新视为异常
        "max_retries": 3,
        "retry_count": 0,
    },
    {
        "name":  "kanban_sync.py",
        "match": "kanban_sync.py daemon",
        "cmd":   ["python3", "scripts/kanban_sync.py", "daemon"],
        "max_retries": 3,
        "retry_count": 0,
    },
]

def log(msg):
    print(f"[{time.strftime('%Y-%m-%d %H:%M:%S')}] {msg}", flush=True)

def is_running(match_str):
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
    if not is_running(proc["match"]):
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

def main():
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
