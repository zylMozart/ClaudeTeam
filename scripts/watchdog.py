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
from config import PROJECT_ROOT, LARK_CLI

CHECK_INTERVAL = 60  # 秒

# 构建带 profile 的 lark-cli event 命令
_lark_event_cmd = " ".join(LARK_CLI) + (
    " event +subscribe "
    "--event-types im.message.receive_v1 "
    "--compact --quiet --force --as bot"
)

PROCS = [
    {
        "name":  "router (lark-cli event | router)",
        "match": "feishu_router.py",
        "cmd":   ["bash", "-c",
                  f"{_lark_event_cmd} "
                  "| python3 scripts/feishu_router.py --stdin"],
        "pid_file": os.path.join(os.path.dirname(__file__), ".router.pid"),
        # 事件心跳文件:router 每处理一条事件就 utime 一次。1800s 没更新
        # = WebSocket 静默死亡(Docker+云 NAT conntrack 过期场景),触发重启。
        # router 启动时会从 cursor 文件补抓断联期间错过的群聊消息,所以
        # 这次重启对用户是透明的,不会丢消息。
        # 调优提示:如果群聊整天安静,误重启代价很低(catchup 会兜底);如果
        # 群聊很活跃,这个门槛几乎永远不会被触发。
        "health_file": os.path.join(os.path.dirname(__file__), ".router.heartbeat"),
        "health_stale_secs": 1800,
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
    # 心跳超时触发的"重启"实际上进程可能还活着(比如 router 的 WebSocket
    # 悄悄死了但 python 进程还在 stdin 上阻塞等事件)。必须先把旧的杀干净,
    # 否则新进程在 acquire_pid_lock() 时会看到旧 PID 还活着,直接 sys.exit(1),
    # 导致 watchdog 每次 health 检查都 restart 一次但都拉不起新的。
    pid_file = proc.get("pid_file")
    if pid_file and os.path.exists(pid_file):
        try:
            with open(pid_file) as f:
                old_pid = int(f.read().strip())
            # 先尝试 SIGTERM 给它清理的机会,不行再 SIGKILL。对 bash pipeline
            # 里的 lark-cli 也要一起干掉,否则管道左侧会孤儿,产生新旧两套
            # lark-cli event 订阅争抢同一个 WebSocket。
            os.kill(old_pid, signal.SIGTERM)
            time.sleep(1)
            try:
                os.kill(old_pid, 0)
                os.kill(old_pid, signal.SIGKILL)  # 还活着,强杀
            except OSError:
                pass
            log(f"   🔪 杀掉旧 {proc['name']} (pid {old_pid})")
        except (ValueError, OSError, FileNotFoundError):
            pass
        # 清理残留的 pid 文件,防止新 router 启动后 acquire_pid_lock 误判
        try:
            os.remove(pid_file)
        except OSError:
            pass
    # 同时清理孤儿的 lark-cli event 订阅子进程(router pipeline 左半部分)。
    # 它们不在 pid_file 里,但和 router 是同一 bash -c 的兄弟。不依赖 pkill
    # (容器 base 镜像未必有),直接扫 /proc 干掉匹配的 cmdline。
    import glob as _glob
    my_pid = os.getpid()
    for proc_dir in _glob.glob("/proc/[0-9]*"):
        try:
            with open(f"{proc_dir}/cmdline", "rb") as f:
                cmdline = f.read().decode("utf-8", errors="ignore")
        except OSError:
            continue
        if "lark-cli" in cmdline and "event" in cmdline and "+subscribe" in cmdline:
            try:
                pid = int(os.path.basename(proc_dir))
                if pid != my_pid:
                    os.kill(pid, signal.SIGKILL)
            except (OSError, ValueError):
                pass
    time.sleep(0.5)

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
