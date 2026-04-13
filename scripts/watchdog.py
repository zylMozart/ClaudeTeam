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
import sys, os, time, glob, subprocess, atexit, signal

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
        # 事件心跳: router 每次从 WebSocket 收到事件(即使被过滤)都会
        # os.utime 这个文件。1800s 没更新 = WebSocket 静默死亡(Docker+云
        # NAT conntrack 过期场景),触发重启。router 启动时会从 cursor 文件
        # 补抓断联期间错过的消息,重启对用户透明不丢消息。
        # cursor 文件一物两用: content 是"最后成功路由的本团队消息时间",
        # mtime 是"最后收到任何 WebSocket 事件的时间"。watchdog 只看 mtime。
        "health_file": os.path.join(os.path.dirname(__file__), ".router.cursor"),
        "health_stale_secs": 1800,
        # 刚重启完的 grace period:距离上次重启 < grace_secs 时只查 PID 存活,
        # 不查 health_file 新鲜度。避免 router 新进程还没来得及 touch cursor
        # 就被 watchdog 再次误判为"心跳超时"连锁重启。
        "restart_grace_secs": 120,
        "max_retries": 3,
        "retry_count": 0,
        "last_restart_ts": 0,
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

def _kill_by_pid_file(pid_file, label):
    """SIGTERM → (1s) → SIGKILL the PID in pid_file, then remove the file.
    No-op if the file or PID don't exist. Used before restart_process to
    make sure a stuck-alive process doesn't block the new one from acquiring
    the same pid lock.
    """
    if not pid_file:
        return
    try:
        with open(pid_file) as f:
            old_pid = int(f.read().strip())
    except (FileNotFoundError, ValueError, OSError):
        return
    try:
        os.kill(old_pid, signal.SIGTERM)
        time.sleep(1)
        try:
            os.kill(old_pid, 0)
            os.kill(old_pid, signal.SIGKILL)
        except OSError:
            pass
        log(f"   🔪 杀掉旧 {label} (pid {old_pid})")
    except OSError:
        pass
    try:
        os.remove(pid_file)
    except OSError:
        pass


def _kill_orphan_lark_subscribers():
    """Scan /proc and SIGKILL any `lark-cli event +subscribe` processes.
    These don't live in any pid_file — they're the left half of the
    `lark-cli ... | feishu_router.py` bash pipeline, so when we kill the
    router we must also kill them, otherwise the new pipeline spawns a
    second lark-cli and two WebSocket subscriptions race.

    Walks /proc instead of shelling out to pkill so we don't depend on
    procps-ng being in the container base image.
    """
    my_pid = os.getpid()
    for proc_dir in glob.glob("/proc/[0-9]*"):
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


def restart_process(proc):
    # 心跳超时触发重启时,旧进程往往还"活着"(router 的 python 在 read(stdin)
    # 上阻塞,lark-cli 在 read(websocket) 上阻塞)。必须先把旧的杀干净,否则
    # 新进程 acquire_pid_lock 看到旧 pid 还在就 sys.exit(1),watchdog 每次
    # 循环都 restart 却永远拉不起新的。
    _kill_by_pid_file(proc.get("pid_file"), proc["name"])
    _kill_orphan_lark_subscribers()

    subprocess.Popen(
        proc["cmd"],
        cwd=PROJECT_ROOT,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
        start_new_session=True,
    )
    proc["last_restart_ts"] = time.time()
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
    # 刚重启完的冷却期:只查 PID 存活,跳过 health_file 新鲜度检查。
    grace_secs = proc.get("restart_grace_secs", 0)
    if grace_secs > 0:
        since_restart = time.time() - proc.get("last_restart_ts", 0)
        if since_restart < grace_secs:
            return True
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
