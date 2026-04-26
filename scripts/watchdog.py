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
from config import PROJECT_ROOT, LARK_CLI, PID_DIR

CHECK_INTERVAL = 60  # 秒

# ADR watchdog_startup_grace: 冷启动时 router pipeline (lark-cli 冷 npm 解
# 析 + feishu_router.py import) 可能 ≥ 30s 才 touch .router.pid。watchdog 若
# 立刻跑首轮 check,会把还没写 pid 的合法 router 误判为死,进 _kill_orphan_
# lark_subscribers 把正在 spawn 的子进程半杀掉。加 startup grace 等 router
# 进入稳态。设 WATCHDOG_STARTUP_GRACE_SECS=0 可关闭(测试/紧急场景)。
STARTUP_GRACE_SECS = int(os.environ.get("WATCHDOG_STARTUP_GRACE_SECS", "60"))

# ── 测试隔离开关 (ADR watchdog_testing_env_guard) ──────────────
# 设 WATCHDOG_TESTING=1 会让 _send_manager_alert 跳过真实 subprocess 调用,
# 只打本地日志。这是 belt-and-suspenders 的 belt 层 —— 防止新测试作者忘记
# patch subprocess.run/Popen 时误发真实消息到 manager inbox。
#
# ⚠️ 这不是测试可以依赖的唯一保护。测试仍然必须把 subprocess.run/Popen patch
# 成 AssertionError,让泄漏 loud-fail 而不是 silent-skip。本开关只是最后一道兜底。
#
# production 启动时绝对不应该设这个变量。任何启动脚本(start-team.sh、
# docker-entrypoint.sh)如果误设它,watchdog 的告警会彻底失效,整个系统变成
# "静默故障",这是本 ADR 最大的部署风险。main() 启动时会打一条显眼警告。
#
# 严格 "1" 比较,不要用 bool(os.environ.get(...)) —— 后者会把
# WATCHDOG_TESTING=0 / WATCHDOG_TESTING=false 都当成真值,是经典坑。
TESTING = os.environ.get("WATCHDOG_TESTING") == "1"

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
        "pid_file": os.path.join(PID_DIR, ".router.pid"),
        # 事件心跳: router 每次从 WebSocket 收到事件(即使被过滤)都会
        # os.utime 这个文件。1800s 没更新 = WebSocket 静默死亡(Docker+云
        # NAT conntrack 过期场景),触发重启。router 启动时会从 cursor 文件
        # 补抓断联期间错过的消息,重启对用户透明不丢消息。
        # cursor 文件一物两用: content 是"最后成功路由的本团队消息时间",
        # mtime 是"最后收到任何 WebSocket 事件的时间"。watchdog 只看 mtime。
        "health_file": os.path.join(PID_DIR, ".router.cursor"),
        "health_stale_secs": 1800,
        # 刚重启完的 grace period:距离上次重启 < grace_secs 时只查 PID 存活,
        # 不查 health_file 新鲜度。避免 router 新进程还没来得及 touch cursor
        # 就被 watchdog 再次误判为"心跳超时"连锁重启。
        "restart_grace_secs": 120,
        # ADR watchdog_max_retries_cooldown: burst + cooldown 状态机
        # max_retries   — 一个 burst 窗口内的重启配额
        # cooldown_secs — burst 耗尽后的静默冷却时长,结束后自动重新 burst
        "max_retries":    3,
        "cooldown_secs":  600,
        # 运行时状态(watchdog 自己维护,初始 0)
        "retry_count":         0,
        "last_restart_ts":     0,
        "cooldown_start_ts":   0,
    },
    {
        "name":  "kanban_sync.py",
        "match": "kanban_sync.py daemon",
        "cmd":   ["python3", "scripts/kanban_sync.py", "daemon"],
        "pid_file": os.path.join(PID_DIR, ".kanban_sync.pid"),
        "max_retries":    3,
        "cooldown_secs":  600,
        "retry_count":         0,
        "cooldown_start_ts":   0,
    },
]

def log(msg):
    print(f"[{time.strftime('%Y-%m-%d %H:%M:%S')}] {msg}", flush=True)

def is_running_by_pid_file(pid_file, match_str=None):
    """通过 PID 锁文件检测进程是否存活。

    Bug 14 修复: 只做 `kill -0` 检查会被 PID 复用误导 —— 原进程死亡后,
    同一个 PID 被系统分配给别的 (比如一个 claude agent), kill -0 仍然返回
    存活,watchdog 就认为目标进程健康,永远不重启。表现: router 死 18 分钟
    也无人救。

    修法: 传入 match_str 时,额外读 /proc/<pid>/cmdline 验证命令行包含
    预期子串。找不到 /proc (非 Linux) 或 cmdline 不匹配都算进程已死。
    match_str=None 时保留老行为,仅做 kill -0 检查。
    """
    if not os.path.exists(pid_file):
        return False
    try:
        with open(pid_file) as f:
            pid = int(f.read().strip())
        os.kill(pid, 0)
    except (ValueError, OSError):
        return False
    if match_str:
        try:
            with open(f"/proc/{pid}/cmdline", "rb") as f:
                cmdline = f.read().decode("utf-8", errors="ignore").replace("\0", " ")
            if match_str not in cmdline:
                return False
        except (FileNotFoundError, PermissionError):
            return False
    return True

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


def _is_lark_subscribe(pid):
    try:
        with open(f"/proc/{pid}/cmdline", "rb") as f:
            c = f.read().decode("utf-8", errors="ignore")
    except OSError:
        return False
    return "lark-cli" in c and "event" in c and "+subscribe" in c


def _kill_orphan_lark_subscribers():
    """SIGKILL `lark-cli event +subscribe` 进程, 收窄作用域版 (ADR
    watchdog_startup_grace).

    老语义 (全 /proc 盲杀) 在 watchdog 首轮 startup race 里会把 tmux pipe
    正在 spawn 的合法 lark-cli 子进程一起杀掉, 造成半 kill 孤儿 + re-spawn
    双 pipeline. 新语义:
      - router pid 文件存在 + 活 → 只杀 router 进程树的 lark-cli 后代
        (正常应为空集, 作为 defense-in-depth)
      - router 死 / pid 文件缺 → 扫 /proc 但只杀 ppid==1 的真孤儿
    搭配 main() 的 STARTUP_GRACE_SECS 双层防御.
    """
    my_pid = os.getpid()
    try:
        with open(os.path.join(PID_DIR, ".router.pid")) as f:
            router_pid = int(f.read().strip())
        os.kill(router_pid, 0)
    except (OSError, ValueError):
        router_pid = None

    if router_pid:
        tree, frontier = {router_pid}, [router_pid]
        while frontier:
            for cf in glob.glob(f"/proc/{frontier.pop()}/task/*/children"):
                try:
                    kids = open(cf).read().split()
                except OSError:
                    continue
                for tok in kids:
                    try:
                        cpid = int(tok)
                    except ValueError:
                        continue
                    if cpid not in tree:
                        tree.add(cpid)
                        frontier.append(cpid)
        victims = [p for p in tree if p != router_pid and _is_lark_subscribe(p)]
    else:
        victims = []
        for proc_dir in glob.glob("/proc/[0-9]*"):
            try:
                pid = int(os.path.basename(proc_dir))
            except ValueError:
                continue
            if pid == my_pid or not _is_lark_subscribe(pid):
                continue
            try:
                with open(f"/proc/{pid}/status") as f:
                    for line in f:
                        if line.startswith("PPid:"):
                            if line.split()[1] == "1":
                                victims.append(pid)
                            break
            except OSError:
                continue

    for pid in victims:
        if pid == my_pid:
            continue
        try:
            os.kill(pid, signal.SIGKILL)
        except OSError:
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

def _send_manager_alert(msg, log_label):
    """向 manager 发送一条自定义告警消息,按 feishu_msg.py 退出码分流日志。

    参数
    ----
    msg       : 完整的告警文案(含 '[watchdog] ...' 前缀),直接透传给 send
    log_label : 日志里的动作描述,例如 '<proc> 重启' / '<proc> 进入 cooldown',
                只用于 log 行的可读性,不进消息本体

    WATCHDOG_TESTING=1 时跳过真实 subprocess 调用,只打本地 log —— 见
    ADR watchdog_testing_env_guard.md。

    退出码语义（与 feishu_msg.py 的 _check_lark_result 约定对齐）:
      0 → 收件箱写入 + 群通知都成功
      1 → 主写入失败(收件箱都没落库),告警很可能没送达,日志留痕让人看得到
      2 → 收件箱已写但群通知失败,告警仍在 manager 的 inbox 里等待,日志降级
          成 warning 即可,不要重发(会产生重复 Bitable 记录)
    """
    if TESTING:
        # 打一条显眼的本地日志,让测试作者一眼看到 belt 拦截了。
        # 如果测试里意外命中这条日志,说明测试的 mock 层有漏 —— 应该
        # 把 mock 下沉到 _send_manager_alert 或 subprocess.run。
        log(f"🧪 [TESTING] 已跳过真实 manager 告警: {log_label} — {msg[:120]}")
        return

    r = subprocess.run(
        ["python3", "scripts/feishu_msg.py",
         "send", "manager", "watchdog", msg, "高"],
        cwd=PROJECT_ROOT,
        capture_output=True, text=True,
    )
    if r.returncode == 0:
        log(f"📨 已通知 manager: {log_label}")
    elif r.returncode == 2:
        log(f"⚠️ 已通知 manager(收件箱OK,群通知失败): {log_label}")
    else:
        err = (r.stderr or "").strip()[:300] or (r.stdout or "").strip()[:300] or "(无输出)"
        log(f"🚨 通知 manager 失败 (exit={r.returncode}): {log_label} — {err}")


def notify_manager(proc_name):
    """burst 分支: 发一条 '<proc> 已崩溃并自动重启' 的默认模板告警。

    这个入口只负责 burst 场景。进 cooldown 时调用方应直接调 _send_manager_alert
    传入自定义文案,避免告警文案互相污染(reviewer CR#1)。
    """
    _send_manager_alert(
        f"[watchdog] {proc_name} 已崩溃并自动重启，请确认运行状态。",
        log_label=f"{proc_name} 重启",
    )

def is_healthy(proc):
    """进程存在 + 健康检查通过"""
    pid_file = proc.get("pid_file")
    if pid_file:
        # 传 match 防 PID 复用误判 (Bug 14)
        if not is_running_by_pid_file(pid_file, proc.get("match")):
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
    """ADR watchdog_max_retries_cooldown: burst + cooldown 状态机.

    状态: HEALTHY -> BURSTING (最多 max_retries 次 restart) -> COOLDOWN
          (cooldown_secs 内静默) -> BURSTING (自动重新 burst) -> ...
          任何时刻 is_healthy=True 都立即重置 retry_count + 退出 cooldown.

    告警节律: burst 每次 restart 发一条 notify,进入 cooldown 时发一条 notify
    告知用户已进入静默,cooldown 期间 0 次 notify. 恢复健康时打 log 但不 notify.
    """
    all_ok = True
    for proc in PROCS:
        name = proc["name"]
        healthy = is_healthy(proc)

        if healthy:
            # 健康恢复: 无条件退出 cooldown + 重置 burst 计数
            if proc.get("retry_count", 0) > 0 or proc.get("cooldown_start_ts", 0) > 0:
                log(f"✅ {name} 恢复健康，重置重试计数")
                proc["retry_count"] = 0
                proc["cooldown_start_ts"] = 0
            continue  # 这个 proc 没事，看下一个

        # ── 不健康分支 ──
        all_ok = False
        now = time.time()
        cooldown_start = proc.get("cooldown_start_ts", 0)
        cooldown_secs = proc.get("cooldown_secs", 600)

        if cooldown_start > 0:
            elapsed = now - cooldown_start
            if elapsed < cooldown_secs:
                # 静默等待: 不重启不告警,日志一行即可,避免刷屏
                remaining = int(cooldown_secs - elapsed)
                log(f"⏸  {name} 仍在 cooldown (剩余 {remaining}s)，跳过本轮")
                continue
            # cooldown 结束: 重置状态,当作全新开始 burst
            log(f"🔁 {name} cooldown 结束 ({cooldown_secs}s)，重新开始重启 burst")
            proc["retry_count"] = 0
            proc["cooldown_start_ts"] = 0
            # 掉落到下面的 burst 逻辑

        # burst 逻辑: max_retries 配额内的快速重启
        proc["retry_count"] = proc.get("retry_count", 0) + 1
        max_retries = proc.get("max_retries", 3)

        if proc["retry_count"] > max_retries:
            # burst 耗尽,进 cooldown,告警发且仅发一次
            log(f"🚨 {name} 连续 {max_retries} 次重启失败，进入 cooldown ({cooldown_secs}s)")
            # 直接走自定义文案,不走 notify_manager 的默认 "已崩溃并自动重启" 模板,
            # 避免两段文案拼在一起自相矛盾(reviewer CR#1)
            _send_manager_alert(
                f"[watchdog] {name} 连续 {max_retries} 次重启失败，已进入 "
                f"{cooldown_secs}s cooldown，期间 watchdog 不会重试。"
                f"cooldown 结束后自动重新尝试。",
                log_label=f"{name} 进入 cooldown",
            )
            proc["cooldown_start_ts"] = now
            continue

        log(f"💀 检测到异常: {name} (第 {proc['retry_count']} 次)")
        restart_process(proc)
        time.sleep(2)
        notify_manager(name)

    if all_ok:
        log("✅ 所有守护进程运行正常")

_PID_FILE = os.path.join(PID_DIR, ".watchdog.pid")

def _acquire_pid_lock():
    if os.path.exists(_PID_FILE):
        try:
            with open(_PID_FILE) as f:
                old_pid = int(f.read().strip())
            os.kill(old_pid, 0)
            # PID 复用校验 (同 074a7dc 给 router 加的 match_str):
            # compose down/up 后旧 PID 可能被不相关进程复用,kill -0 仍返回存活。
            try:
                with open(f"/proc/{old_pid}/cmdline", "rb") as f:
                    cmdline = f.read().decode("utf-8", errors="ignore")
                if "watchdog.py" not in cmdline:
                    raise OSError("PID reuse: not watchdog")
            except (FileNotFoundError, PermissionError):
                raise OSError("proc gone")
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
    if STARTUP_GRACE_SECS > 0:
        log(f"⏳ Startup grace {STARTUP_GRACE_SECS}s，避免和 router 冷启动抢 race")
        time.sleep(STARTUP_GRACE_SECS)
    if TESTING:
        # 显眼警告: production 绝不应该看到这一行。如果线上看到了,立刻查
        # 这个环境变量是哪里设的并拿掉。ADR watchdog_testing_env_guard 对
        # 应的 belt 层被意外打开 = watchdog 告警彻底失效 = 静默故障。
        log("🚨🚨🚨 WATCHDOG_TESTING=1 已启用 — 所有 manager 告警将被吞掉!")
        log("        这不应该出现在 production,请检查启动脚本 env。")
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
