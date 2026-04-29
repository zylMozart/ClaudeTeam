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

_SCRIPT_DIR = os.path.dirname(__file__)
sys.path.insert(0, _SCRIPT_DIR)
_SRC_DIR = os.path.join(os.path.dirname(_SCRIPT_DIR), "src")
if _SRC_DIR not in sys.path:
    sys.path.insert(0, _SRC_DIR)

from claudeteam.supervision import watchdog_state as _watchdog_state
from claudeteam.supervision import watchdog_specs as _watchdog_specs
from claudeteam.supervision import watchdog_health as _watchdog_health
from claudeteam.supervision import watchdog_effect_plan as _watchdog_effect_plan
from claudeteam.supervision import watchdog_alert_delivery as _watchdog_alert_delivery
from claudeteam.supervision import watchdog_alert_request as _watchdog_alert_request
from claudeteam.supervision import watchdog_daemon as _watchdog_daemon
from claudeteam.supervision import watchdog_messages as _watchdog_messages
from claudeteam.supervision import watchdog_orphans as _watchdog_orphans
from claudeteam.supervision import watchdog_proc_match as _watchdog_proc_match
from claudeteam.runtime.config import PROJECT_ROOT, LARK_CLI
from claudeteam.runtime.paths import legacy_script_state_file, runtime_state_file, runtime_state_dir, ensure_parent

CHECK_INTERVAL = 60  # 秒
_state_dir = runtime_state_dir()
ROUTER_PID_FILE = str(_state_dir / "router.pid")
ROUTER_CURSOR_FILE = str(_state_dir / "router.cursor")
LEGACY_ROUTER_PID_FILE = legacy_script_state_file(".router.pid")
KANBAN_PID_FILE = str(_state_dir / "kanban_sync.pid")
WATCHDOG_PID_FILE = str(_state_dir / "watchdog.pid")

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

# tmux_target 让 restart_process 走 send-keys 复活到 pane (F-ROUTER-1)。
# 步骤 D：daemon 全部转 nohup 后端，router 不再有 tmux pane 可送 keys。默认走
# Popen fallback；如需保留旧 tmux send-keys 行为（rollback 场景），显式设置
# CLAUDETEAM_ROUTER_TMUX_TARGET=<session>:router 即可重新启用。
_ROUTER_TMUX_TARGET = os.environ.get("CLAUDETEAM_ROUTER_TMUX_TARGET", "").strip()

_PROC_SPECS = _watchdog_specs.build_process_specs(
    lark_cli=LARK_CLI,
    router_pid_file=ROUTER_PID_FILE,
    router_cursor_file=ROUTER_CURSOR_FILE,
    kanban_pid_file=KANBAN_PID_FILE,
    router_tmux_target=_ROUTER_TMUX_TARGET,
)

def _env_enabled(name):
    return _watchdog_specs.env_enabled(name, env=os.environ)


def _enabled_procs(procs=None):
    return _watchdog_specs.filter_enabled_processes(
        _PROC_SPECS if procs is None else procs,
        env=os.environ,
    )


PROCS = _enabled_procs()

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
    return _watchdog_proc_match.is_lark_subscribe_cmdline(c)


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
    router_pid = None
    for path in (ROUTER_PID_FILE, LEGACY_ROUTER_PID_FILE):
        try:
            with open(path) as f:
                router_pid = int(f.read().strip())
            os.kill(router_pid, 0)
            break
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
        is_subscribe = {pid: _is_lark_subscribe(pid) for pid in tree}
        victims = _watchdog_orphans.select_router_tree_victims(
            tree_pids=tree,
            router_pid=router_pid,
            my_pid=my_pid,
            is_lark_subscribe=is_subscribe,
        )
    else:
        candidates = []
        ppid_by_pid = {}
        is_subscribe = {}
        for proc_dir in glob.glob("/proc/[0-9]*"):
            try:
                pid = int(os.path.basename(proc_dir))
            except ValueError:
                continue
            if pid == my_pid:
                continue
            subscribed = _is_lark_subscribe(pid)
            is_subscribe[pid] = subscribed
            if not subscribed:
                continue
            candidates.append(pid)
            try:
                with open(f"/proc/{pid}/status") as f:
                    status_text = f.read()
                ppid_by_pid[pid] = _watchdog_orphans.parse_ppid_from_status_text(status_text)
            except OSError:
                continue
        victims = _watchdog_orphans.select_orphan_victims(
            candidate_pids=candidates,
            my_pid=my_pid,
            is_lark_subscribe=is_subscribe,
            ppid_by_pid=ppid_by_pid,
        )

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

    target = (proc.get("tmux_target") or "").strip()
    if target:
        # F-ROUTER-1: 在 tmux pane 内复活,蒙眼 capture-pane 看得到新 banner;
        # send-keys 失败 (pane 锁/session 没了/tmux 不在 PATH) 立刻 fallback
        # 到旧 Popen,保证老部署行为不变。
        # F-FIX-2: router 的 launch 命令必须从 scripts/lib/router_launch.sh
        # 取,因为它读 runtime_config.json 的 lark_profile 注入 --profile;
        # build_lark_event_subscribe_cmd 不带 profile, 多 profile host
        # (team01/team02/life-pm/...) watchdog 自愈会订阅错 App。
        launch_str = ""
        cmd_list = proc.get("cmd") or []
        is_router_pipeline = (isinstance(cmd_list, list) and cmd_list[:2] == ["bash", "-c"]
                              and len(cmd_list) >= 3 and "feishu_router.py" in cmd_list[2])
        if is_router_pipeline:
            try:
                rs = subprocess.run(
                    ["bash", os.path.join(PROJECT_ROOT, "scripts/lib/router_launch.sh")],
                    cwd=PROJECT_ROOT, capture_output=True, text=True, timeout=5,
                )
                candidate = (rs.stdout or "").strip()
                if rs.returncode == 0 and candidate and "feishu_router.py" in candidate:
                    launch_str = candidate
                else:
                    log(f"⚠️ router_launch.sh 输出异常 (rc={rs.returncode}); fallback 到内置 cmd")
            except Exception as e:
                log(f"⚠️ router_launch.sh 调用失败 ({e}); fallback 到内置 cmd")
        if not launch_str:
            if isinstance(cmd_list, list) and cmd_list[:2] == ["bash", "-c"] and len(cmd_list) >= 3:
                launch_str = cmd_list[2]
            else:
                launch_str = " ".join(cmd_list) if isinstance(cmd_list, list) else str(cmd_list)
        try:
            subprocess.run(["tmux", "send-keys", "-t", target, "C-c"],
                           check=False, timeout=5)
            time.sleep(0.3)
            subprocess.run(["tmux", "send-keys", "-t", target, launch_str, "Enter"],
                           check=True, timeout=5)
            proc["last_restart_ts"] = time.time()
            log(f"🔄 已重启: {proc['name']} (via tmux {target})")
            return
        except Exception as e:
            log(f"⚠️ tmux send-keys 重启 {target} 失败 ({e}); fallback 到 Popen")

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
    normalized_msg = _watchdog_alert_request.normalize_alert_message(msg)
    normalized_log_label = _watchdog_alert_request.normalize_alert_log_label(log_label)

    if TESTING:
        # 打一条显眼的本地日志,让测试作者一眼看到 belt 拦截了。
        # 如果测试里意外命中这条日志,说明测试的 mock 层有漏 —— 应该
        # 把 mock 下沉到 _send_manager_alert 或 subprocess.run。
        log(
            _watchdog_alert_request.build_testing_skip_log_line(
                normalized_log_label,
                normalized_msg,
                preview_limit=120,
            )
        )
        return

    r = subprocess.run(
        _watchdog_alert_request.build_manager_alert_send_cmd(normalized_msg),
        cwd=PROJECT_ROOT,
        capture_output=True, text=True,
    )
    log(
        _watchdog_alert_delivery.build_alert_delivery_log_line(
            r.returncode,
            normalized_log_label,
            r.stdout,
            r.stderr,
        )
    )


def notify_manager(proc_name):
    """burst 分支: 发一条 '<proc> 已崩溃并自动重启' 的默认模板告警。

    这个入口只负责 burst 场景。进 cooldown 时调用方应直接调 _send_manager_alert
    传入自定义文案,避免告警文案互相污染(reviewer CR#1)。
    """
    _send_manager_alert(
        _watchdog_messages.build_burst_alert(proc_name),
        log_label=f"{proc_name} 重启",
    )

def _pgrep_alive(match_str):
    """pgrep 兜底判活: PID 文件缺失/损坏/PID 复用时, 用 pgrep -f 确认进程是否仍在。"""
    if not match_str:
        return False
    try:
        r = subprocess.run(["pgrep", "-f", match_str], capture_output=True, timeout=5)
        return r.returncode == 0
    except Exception:
        return False


def is_healthy(proc):
    """进程存在 + 健康检查通过"""
    pid_file = proc.get("pid_file")
    if pid_file:
        # 传 match 防 PID 复用误判 (Bug 14)
        if not is_running_by_pid_file(pid_file, proc.get("match")):
            # pgrep 兜底: PID 文件可能落后于实际进程状态
            if not _pgrep_alive(proc.get("match")):
                return False
            log(f"ℹ️ {proc['name']} PID 文件异常但 pgrep 找到进程, 视为存活")
    elif not is_running(proc["match"]):
        return False
    now = time.time()
    restart_grace_secs = float(proc.get("restart_grace_secs", 0) or 0)
    last_restart_ts = float(proc.get("last_restart_ts", 0) or 0)
    health_stale_secs = float(proc.get("health_stale_secs", 300) or 300)
    health_file = proc.get("health_file")
    health_file_age_secs = None
    if health_file and os.path.exists(health_file):
        health_file_age_secs = now - os.path.getmtime(health_file)
    decision = _watchdog_health.decide_health_file_state(
        now=now,
        last_restart_ts=last_restart_ts,
        restart_grace_secs=restart_grace_secs,
        health_file_age_secs=health_file_age_secs,
        health_stale_secs=health_stale_secs,
    )
    if decision.skip_health_file_check:
        return True
    if decision.health_file_stale:
        log(f"⚠️ {proc['name']} 健康检查失败：输出文件 {health_file_age_secs:.0f}s 未更新")
        return False
    return True

# ── 告警去重/节流 ────────────────────────────────────────────────
# 每个进程独立维护: 上次告警时间 + 是否处于 unhealthy 状态。
# 同一进程 300s 内不重复告警 (burst 期间每分钟检查一次, 5 分钟最多 1 条)。
# 进程从 unhealthy → healthy 时发一条恢复通知。
_ALERT_THROTTLE_SECS = 300
_alert_state: dict = {}  # proc_name -> {"last_alert_ts": float, "was_unhealthy": bool}


def _throttled_alert(proc_name, msg, log_label):
    """发告警, 但同一进程 _ALERT_THROTTLE_SECS 内最多发一次。"""
    now = time.time()
    st = _alert_state.setdefault(proc_name, {"last_alert_ts": 0, "was_unhealthy": False})
    st["was_unhealthy"] = True
    if now - st["last_alert_ts"] < _ALERT_THROTTLE_SECS:
        log(f"   ⏳ {proc_name} 告警节流中 (距上次 {now - st['last_alert_ts']:.0f}s)")
        return
    st["last_alert_ts"] = now
    _send_manager_alert(msg, log_label)


def _notify_recovered(proc_name):
    """进程恢复健康时发一条恢复通知 (仅当之前处于 unhealthy 状态时)。"""
    st = _alert_state.get(proc_name)
    if not st or not st.get("was_unhealthy"):
        return
    st["was_unhealthy"] = False
    log(f"✅ {proc_name} 已恢复健康")


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
        decision = _watchdog_state.decide_watchdog_state(
            proc,
            healthy=healthy,
            now=time.time(),
        )
        proc["retry_count"] = decision.retry_count
        proc["cooldown_start_ts"] = decision.cooldown_start_ts

        plan = _watchdog_effect_plan.build_effect_plan(
            proc_name=name,
            action=decision.action,
            retry_count=proc["retry_count"],
            cooldown_remaining_secs=decision.cooldown_remaining_secs,
            cooldown_ended=decision.cooldown_ended,
            max_retries=decision.max_retries,
            cooldown_secs=decision.cooldown_secs,
            action_healthy=_watchdog_state.ACTION_HEALTHY,
            action_healthy_reset=_watchdog_state.ACTION_HEALTHY_RESET,
            action_cooldown_wait=_watchdog_state.ACTION_COOLDOWN_WAIT,
            action_enter_cooldown=_watchdog_state.ACTION_ENTER_COOLDOWN,
        )
        for line in plan.log_lines:
            log(line)

        if not plan.mark_unhealthy:
            _notify_recovered(name)
            continue

        # ── 不健康分支 ──
        all_ok = False
        if plan.effect == _watchdog_effect_plan.EFFECT_CONTINUE:
            continue

        if plan.effect == _watchdog_effect_plan.EFFECT_ALERT_ONLY:
            _throttled_alert(
                name,
                _watchdog_messages.build_cooldown_alert(
                    name,
                    decision.max_retries,
                    decision.cooldown_secs,
                ),
                log_label=f"{name} 进入 cooldown",
            )
            continue

        restart_process(proc)
        time.sleep(2)
        log(f"ℹ️ {name} 已自动重启，静默处理（仅 cooldown 时告警）")

    if all_ok:
        log("✅ 所有守护进程运行正常")

_PID_FILE = WATCHDOG_PID_FILE


def _pid_file_is_live_watchdog(path):
    def _read_text(file_path):
        with open(file_path) as f:
            return f.read()

    def _pid_is_alive(pid):
        os.kill(pid, 0)
        return True

    def _read_cmdline(pid):
        with open(f"/proc/{pid}/cmdline", "rb") as f:
            return f.read().decode("utf-8", errors="ignore")

    return _watchdog_daemon.pid_file_is_live(
        path,
        path_exists=os.path.exists,
        read_text=_read_text,
        pid_is_alive=_pid_is_alive,
        read_cmdline=_read_cmdline,
        expected_fragment="watchdog.py",
    )


def _acquire_pid_lock():
    if _pid_file_is_live_watchdog(_PID_FILE):
        try:
            with open(_PID_FILE) as f:
                old_pid = int(f.read().strip())
            log(f"❌ Watchdog 已在运行 (PID {old_pid})")
            sys.exit(1)
        except (ValueError, OSError):
            pass
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

def _shutdown_handler(signum, frame):
    # 收到 stop.sh / kill 的 SIGTERM、Ctrl-C 的 SIGINT 时, 先在日志里留一条
    # '[shutting down]', 让 stop.sh 的调用者能在 watchdog.log 末尾看到优雅退出
    # 证据。sys.exit(0) 触发 atexit 注册的 _cleanup_pid 删 pid 文件。
    log(f"[shutting down] received signal {signum}")
    sys.exit(0)

signal.signal(signal.SIGTERM, _shutdown_handler)
signal.signal(signal.SIGINT, _shutdown_handler)

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
