#!/usr/bin/env python3
"""
Router Daemon — 从 lark-cli event 事件流读取消息，路由到 tmux 窗口

用法（管道模式）：
  lark-cli event +subscribe --event-types im.message.receive_v1 --compact --quiet --force \
    | python3 scripts/feishu_router.py --stdin

也可独立运行（兼容旧模式，自启 lark-cli 子进程）：
  python3 scripts/feishu_router.py
"""
import sys, os, json, time, re, subprocess, atexit, signal, threading, glob
from collections import OrderedDict

sys.path.insert(0, os.path.dirname(__file__))
from config import AGENTS, TMUX_SESSION, PROJECT_ROOT, load_runtime_config, LARK_CLI, PID_DIR
from tmux_utils import inject_when_idle, is_agent_idle, capture_pane
from msg_queue import enqueue_message, has_pending_messages, dequeue_pending, check_manager_unread
from feishu_msg import _lark_run, cmd_say, sanitize_agent_message
from message_renderer import render_inbox_text, render_tmux_prompt
from cli_adapters import adapter_for_agent
import tmux_command
import team_command
import slash_commands

_LIFECYCLE_SH = os.path.join(PROJECT_ROOT, "scripts", "lib", "agent_lifecycle.sh")

IMAGES_DIR = os.path.join(PROJECT_ROOT, "workspace", "shared", "images")

# ── 消息模板常量 ──────────────────────────────────────────────

TPL_AGENT_NOTIFY = (
    "【Router】你有来自 {sender} 的新消息。\n"
    "执行: python3 scripts/feishu_msg.py inbox {agent}\n"
    "消息预览: {preview}"
)

TPL_USER_MSG_SHORT = (
    "【群聊消息】用户在群里对你说:\n{content}\n\n"
    "请直接处理，然后用以下命令回复群里:\n"
    "python3 scripts/feishu_msg.py say {agent} \"<你的回复>\""
)

TPL_USER_MSG_LONG = (
    "【群聊消息】用户在群里发了消息（较长，已保存到文件）。\n"
    "请先读取文件: {file_path}\n"
    "预览: {preview}\n\n"
    "处理完成后用以下命令回复群里:\n"
    "python3 scripts/feishu_msg.py say {agent} \"<你的回复>\""
)

TPL_IMAGE_DOWNLOADED = (
    "【Router 补充】图片已下载。\n"
    "本地路径: {path}\n"
    "你可以使用 Read 工具查看图片。"
)

# ── Router 状态 ──────────────────────────────────────────────

_TEAM_FILE = os.path.join(PROJECT_ROOT, "team.json")

# seen_ids 容量上限。用 OrderedDict 当 LRU,超 cap 时 popitem(last=False) 弹出
# 最早插入的 msg_id (FIFO eviction)。
# 容量推导 (lazy_wake_v2 audit):
#   - 中等活跃团队 ~500 msg/d × 20 天 ≈ 10000 条 dedup 历史
#   - 单条 msg_id ≈ 32 字节,OrderedDict 单槽 ~280 字节,总 RSS ≈ 3 MB 上限
#   - catchup replay 单轮最大 ~1500 条,远低于 10000,弹出后不可能误重 replay
# 改这个数前先看 agents/coder/workspace/lazy_wake_memory_audit.md。
SEEN_IDS_MAX = 10000


class RouterState:
    """封装 Router 的可变状态。"""

    def __init__(self):
        self.bot_open_id = ""
        self.chat_id = ""  # 本团队的 chat_id,用于过滤跨团队事件
        self._team_mtime = 0
        self._agent_names = []
        # OrderedDict 当 LRU dedupe (lazy_wake_v2 audit):set 无界增长,
        # router 跑数月后会吃掉几十 MB 纯为 dedup 服务。换成 OrderedDict 后
        # 超 SEEN_IDS_MAX 时 popitem(last=False) 弹出最早 msg_id,RSS 有上界。
        self.seen_ids = OrderedDict()
        self.first_event_at = None  # Bug 16: 首事件时间戳,用于检测订阅缺失

    def init_bot_id(self):
        """通过 lark-cli 获取 bot 的 open_id（从群成员列表中识别 bot 成员）。

        Bug 14 防御:
        - 先从 runtime_config.json 读缓存,命中就不再探测(ClaudeTeam 的 bot open_id
          跟 App 绑定,不会变)。
        - 超时从 15s 提到 40s,避免 chat.members get 在集群繁忙时被误判为失败。
        - 成功拿到后回写到 runtime_config.json。
        """
        cfg_path = os.path.join(os.path.dirname(__file__), "runtime_config.json")
        try:
            with open(cfg_path) as f:
                cfg = json.load(f)
            chat_id = cfg.get("chat_id", "")
            cached = cfg.get("bot_open_id", "")
            if cached:
                self.bot_open_id = cached
                print(f"🤖 Bot open_id (cached): {cached}")
                return
            if not chat_id:
                print("⚠️ 无 chat_id，自回声过滤将不可用")
                return
            r = subprocess.run(
                LARK_CLI + ["im", "chat.members", "get",
                            "--params", json.dumps({"chat_id": chat_id, "member_id_type": "open_id"}),
                            "--as", "bot", "--page-all", "--format", "json"],
                capture_output=True, text=True, timeout=40)
            if r.returncode == 0:
                d = json.loads(r.stdout)
                items = d.get("data", {}).get("items", [])
                for item in items:
                    if item.get("member_id_type") == "open_id" and not item.get("tenant_key"):
                        self.bot_open_id = item["member_id"]
                        print(f"🤖 Bot open_id: {self.bot_open_id}")
                        cfg["bot_open_id"] = self.bot_open_id
                        with open(cfg_path, "w") as f:
                            json.dump(cfg, f, indent=2, ensure_ascii=False)
                        return
                # chat.members.get 在飞书里经常不把 bot 当作 member 返回(bot_count
                # 另外计数),所以这里拿不到不是致命问题 —— im.message.receive_v1
                # 上游已经过滤 bot 自身消息,这里只是 belt-and-suspenders。
                print("⚠️ 群成员中无 bot 条目(不影响上游事件过滤,自回声防护降级)")
            else:
                print(f"⚠️ 获取群成员失败: {r.stderr.strip()[:120]}")
        except subprocess.TimeoutExpired:
            print("⚠️ 获取 bot info 超时(40s),自回声过滤将不可用")
        except Exception as e:
            print(f"⚠️ 获取 bot info 异常: {e}，自回声过滤将不可用")

    def reload_agents(self):
        """热加载 agent 列表。"""
        try:
            mt = os.path.getmtime(_TEAM_FILE)
            if mt != self._team_mtime:
                with open(_TEAM_FILE) as f:
                    data = json.load(f)
                self._agent_names = list(data.get("agents", {}).keys())
                self._team_mtime = mt
                print(f"🔄 Agent 列表已刷新: {', '.join(self._agent_names)}")
        except Exception as e:
            print(f"⚠️ reload_agents 失败: {e}")
        return self._agent_names

    def is_bot_message(self, sender_id):
        return bool(self.bot_open_id and sender_id == self.bot_open_id)

    def parse_targets(self, text):
        found = []
        for name in self.reload_agents():
            if f"@{name}" in text:
                found.append(name)
        return found

    def parse_sender(self, text):
        m = re.search(r"【(\w[\w-]*)[\s·]", text)
        if m:
            name = m.group(1)
            if name in self.reload_agents():
                return name
        return None


_state = RouterState()

# ── 图片下载（lark-cli） ─────────────────────────────────────

def download_image(message_id, file_key):
    """用 lark-cli 下载图片，返回本地路径或 None。"""
    os.makedirs(IMAGES_DIR, exist_ok=True)
    output_name = f"{int(time.time())}_{message_id[:8]}_{file_key[:8]}"
    r = subprocess.run(
        LARK_CLI + ["im", "+messages-resources-download",
                    "--message-id", message_id,
                    "--file-key", file_key,
                    "--type", "image",
                    "--output", output_name,
                    "--as", "bot"],
        capture_output=True, text=True, timeout=30,
        cwd=IMAGES_DIR)
    if r.returncode != 0:
        print(f"  ⚠️ 图片下载失败: {r.stderr.strip()[:100]}")
        return None
    # 查找下载的文件（lark-cli 可能自动加扩展名）
    for f in os.listdir(IMAGES_DIR):
        if f.startswith(output_name):
            path = os.path.join(IMAGES_DIR, f)
            print(f"  📥 图片已保存: {path}")
            return path
    return None

# ── lazy-wake: 收件时若休眠先唤醒 ──────────────────────────────
# 设计 (lazy_wake_v2 ADR §A.8 / §C):
#   - "休眠" 的物理判定 = 该 agent 没有活的 claude 进程 (pgrep --name)
#   - 唤醒走 scripts/lib/agent_lifecycle.sh wake — 它会 resume saved session
#     并把状态表从"休眠"改回"待命"
#   - 唤醒后必须等 Claude UI 真起来 (tmux pane 出现 "bypass permissions on"
#     或 "? for shortcuts"),否则后续 inject_when_idle 会把消息扔到 bash
#     prompt 里污染历史
#   - 并发护栏: 同时最多 2 个 agent 在唤醒中 (Semaphore),保护 Anthropic API
#     启动配额和容器内 npm 启动并发
#   - 500ms debounce: 同一 agent 在 500ms 内的多条消息复用同一次 wake,而不
#     是触发多次重启 (节省 cache miss 钱)

WAKE_DEBOUNCE_MS = 500
WAKE_READY_TIMEOUT_S = 30
WAKE_MAX_PARALLEL = 2

_wake_sem = threading.Semaphore(WAKE_MAX_PARALLEL)
_wake_lock = threading.Lock()
_wake_state = {}  # agent_name -> {"started_at": ts, "ready_event": Event}


def _agent_has_live_cli(agent_name):
    """判断该 agent 的 tmux 窗口里是否有活的 CLI 进程。

    通过 adapter.process_name() 获取进程名（CC="claude", Kimi="kimi"），
    用 tmux pane PID + /proc PPid 关系定位:
      1. tmux display-message → 拿到该 agent pane 的前台 bash PID
      2. 扫 /proc 找 PPid == 该 bash PID 且 comm == process_name 的子进程

    使用物理事实(进程是否存在)而不是状态表"休眠"字段,避免:
      - 状态表写入失败留下不一致状态
      - supervisor 写"休眠"和实际 kill 之间的窗口期
      - 老板手动 kill 之后状态表来不及刷新
    """
    pids = _cli_pids_in_pane(agent_name)
    return len(pids) > 0


def _pane_bash_pid(agent_name):
    """tmux 窗口的前台 shell PID。窗口不存在返回 None。"""
    try:
        r = subprocess.run(
            ["tmux", "display-message", "-t", f"{TMUX_SESSION}:{agent_name}",
             "-p", "#{pane_pid}"],
            capture_output=True, text=True, timeout=5,
        )
        if r.returncode != 0:
            return None
        return int(r.stdout.strip())
    except (ValueError, Exception):
        return None


def _cli_pids_in_pane(agent_name):
    """返回该 agent pane 下所有 CLI 进程的 PID 列表。"""
    bash_pid = _pane_bash_pid(agent_name)
    if bash_pid is None:
        return []
    proc_name = adapter_for_agent(agent_name).process_name()
    children = {}
    comm_by_pid = {}
    for proc_dir in glob.glob("/proc/[0-9]*"):
        try:
            pid = int(os.path.basename(proc_dir))
        except ValueError:
            continue
        try:
            with open(f"{proc_dir}/status") as f:
                status = f.read()
        except OSError:
            continue
        ppid = None
        for line in status.splitlines():
            if line.startswith("PPid:"):
                try:
                    ppid = int(line.split()[1])
                except (IndexError, ValueError):
                    ppid = None
                break
        if ppid is not None:
            children.setdefault(ppid, []).append(pid)
        try:
            with open(f"{proc_dir}/comm") as f:
                comm_by_pid[pid] = f.read().strip()
        except OSError:
            pass
    result = []
    stack = list(children.get(bash_pid, []))
    seen = set()
    while stack:
        pid = stack.pop()
        if pid in seen:
            continue
        seen.add(pid)
        if comm_by_pid.get(pid) == proc_name:
            result.append(pid)
        stack.extend(children.get(pid, []))
    return result


def _wait_cli_ui_ready(agent_name, timeout_s=WAKE_READY_TIMEOUT_S):
    """轮询 tmux pane,等 CLI UI 出现 adapter 定义的 ready 特征串。"""
    adapter = adapter_for_agent(agent_name)
    markers = adapter.ready_markers()
    proc_name = adapter.process_name()
    not_ready = ("Loading configuration",)
    deadline = time.time() + timeout_s
    while time.time() < deadline:
        pane = capture_pane(TMUX_SESSION, agent_name)
        tail = "\n".join(pane.splitlines()[-30:])
        if any(m in tail for m in not_ready):
            time.sleep(0.5)
            continue
        if any(m in tail for m in markers):
            if proc_name == "kimi":
                time.sleep(1.5)
            return True
        time.sleep(0.5)
    return False


def wake_on_deliver(agent_name):
    """唤醒一个休眠中的 agent,等 Claude UI ready 后返回。

    幂等 + 并发安全:
      - agent 已活 → 立即返回 True
      - 500ms 内有人已经在 wake 同一个 agent → 等同一次 wake 完成
      - 否则: 取 _wake_sem (≤2 并发) → 调 lifecycle wake → 等 UI ready

    返回:
      True  — agent 已 ready (或本来就活着)
      False — wake 失败 / UI 未在 timeout 内出现
    """
    if _agent_has_live_cli(agent_name):
        if adapter_for_agent(agent_name).process_name() == "kimi":
            return _wait_cli_ui_ready(agent_name, timeout_s=min(10, WAKE_READY_TIMEOUT_S))
        return True

    now = time.time()
    spawn_thread = False
    with _wake_lock:
        st = _wake_state.get(agent_name)
        if st and (now - st["started_at"]) * 1000 < WAKE_DEBOUNCE_MS \
                and not st["ready_event"].is_set():
            # 500ms debounce 命中: 复用进行中的 wake
            ev = st["ready_event"]
        else:
            ev = threading.Event()
            _wake_state[agent_name] = {"started_at": now, "ready_event": ev}
            spawn_thread = True

    if spawn_thread:
        def _do_wake():
            try:
                with _wake_sem:
                    print(f"  🌅 wake_on_deliver: 唤醒 {agent_name}")
                    r = subprocess.run(
                        ["bash", _LIFECYCLE_SH, "wake", agent_name],
                        capture_output=True, text=True, timeout=20,
                    )
                    if r.returncode != 0:
                        print(f"  ⚠️ wake_on_deliver: lifecycle wake "
                              f"{agent_name} 退出 {r.returncode}: "
                              f"{(r.stderr or '').strip()[:200]}")
                    if not _wait_cli_ui_ready(agent_name):
                        print(f"  ⚠️ wake_on_deliver: {agent_name} "
                              f"UI 未在 {WAKE_READY_TIMEOUT_S}s 内 ready")
            except subprocess.TimeoutExpired:
                print(f"  ⚠️ wake_on_deliver: lifecycle wake {agent_name} 超时")
            except Exception as e:
                print(f"  ⚠️ wake_on_deliver: {agent_name} 异常: {e}")
            finally:
                ev.set()

        threading.Thread(target=_do_wake, daemon=True).start()

    # 等 ready,上限给个余量避免无限阻塞调用方 (router 主线程不能死)
    return ev.wait(WAKE_READY_TIMEOUT_S + 5)


# ── 触发 tmux 窗口 ────────────────────────────────────────────

def wake_agent(agent_name, message_preview, sender_agent=None,
               full_text=None, msg_id=""):
    """向 agent 投递消息：先尝试直接投递，忙碌则入队"""
    message_preview = sanitize_agent_message(message_preview)
    full_text = sanitize_agent_message(full_text) if full_text else full_text
    # lazy-wake: 若 agent 休眠,先 wake 起来再投递。已活则秒回。
    wake_on_deliver(agent_name)
    if sender_agent:
        prompt = TPL_AGENT_NOTIFY.format(
            sender=sender_agent, agent=agent_name,
            preview=message_preview[:500])
    else:
        content = render_inbox_text(full_text or message_preview)
        if len(content) > 400:
            msg_file = os.path.join(PROJECT_ROOT, "workspace", "shared",
                                    f".router_msg_{agent_name}.txt")
            os.makedirs(os.path.dirname(msg_file), exist_ok=True)
            with open(msg_file, "w", encoding="utf-8") as f:
                f.write(content)
            prompt = TPL_USER_MSG_LONG.format(
                file_path=msg_file,
                preview=render_inbox_text(content[:200]),
                agent=agent_name)
        else:
            prompt = render_tmux_prompt(
                "群聊消息", "用户在群里对你说:", content, agent_name)

    is_user_msg = (sender_agent is None)
    has_pending = has_pending_messages(agent_name)

    if not has_pending:
        ok = inject_when_idle(TMUX_SESSION, agent_name, prompt,
                              wait_secs=15, force_after_wait=False,
                              submit_keys=adapter_for_agent(agent_name).submit_keys())
        if ok:
            print(f"  → 已触发 {agent_name} 窗口（直接投递）")
            return
        detail = getattr(ok, "error", "") or "not submitted"
        print(f"  📥 直接投递未提交 {agent_name}: {detail}，转入队列")

    enqueue_message(agent_name, prompt, msg_id, is_user_msg=is_user_msg)
    if has_pending:
        print(f"  📥 消息已入队 {agent_name}（队列有积压，保证 FIFO）")
    else:
        print(f"  📥 消息已入队 {agent_name}（agent 忙碌，等待投递）")

# ── 处理单条事件 ──────────────────────────────────────────────

def handle_event(event):
    """处理一条 --compact 格式的事件 JSON。

    ADR: feishu_router_dedup_order.md — 严格两阶段契约。
    阶段 1 必须在任何 return 之前无条件运行,阶段 2 才允许 early return。
    """
    # ─── 阶段 1: 无条件 liveness 证明 ─────────────────────────────
    # 任何从 WebSocket / stdin / catchup 到达的事件都要在这里刷心跳,
    # 包括会被 dedup / chat_id filter / bot 自发消息 filter / 无效 msg_id
    # 早退的事件。原因: router 的心跳是"最后一次从输入流读到东西的时间"
    # 而不是"最后一次路由成功的时间"—— watchdog 只判断 router 有没有卡死,
    # 跟事件是否命中业务逻辑完全无关。如果把心跳刷新放在 dedup 之后,
    # 重复事件不会刷心跳,群聊冷清 + catchup 轮询 replay 的场景下,
    # router 每 30 分钟会被 watchdog 误杀一次。
    # first_event_at 同理 — 任何事件到达都证明订阅活着,用于 _event_watchdog
    # 的"启动 45 秒内是否收到事件"判断。
    if _state.first_event_at is None:
        _state.first_event_at = time.time()
    _refresh_heartbeat()

    # ─── 阶段 2: 内容过滤(允许 early return) ──────────────────────
    # 以下所有检查都可能 return,不影响心跳。
    # 2a. 无效 msg_id — 放最前,便宜且挡掉损坏事件
    msg_id = event.get("message_id", "")
    if not msg_id:
        return
    # 2b. 去重(seen_ids) — 查完立即 add,避免未来并发下 TOCTOU
    # OrderedDict 当 LRU: 超 SEEN_IDS_MAX 弹最早一条 (FIFO),RSS 上界 ~3 MB。
    if msg_id in _state.seen_ids:
        return
    _state.seen_ids[msg_id] = None
    if len(_state.seen_ids) > SEEN_IDS_MAX:
        _state.seen_ids.popitem(last=False)
    # 2c. 跨团队 chat 过滤 — 多团队共用同一 Feishu App 时必须做
    event_chat_id = event.get("chat_id", "")
    if _state.chat_id and event_chat_id and event_chat_id != _state.chat_id:
        return
    # 2d. bot 自发消息过滤
    sender_id = event.get("sender_id", "")
    if _state.is_bot_message(sender_id):
        return

    # ─── 阶段 3: 内容解析与路由 ──────────────────────────────────
    # --compact 模式下 text 字段已解析好
    text = event.get("text", event.get("content", ""))
    msg_type = event.get("message_type", "text")
    text = sanitize_agent_message(text)

    if not text:
        return

    # 斜杠命令统一前置过滤 — 不路由任何 agent,不走 LLM。
    # .claude/hooks 覆盖 manager 本机输入;这里覆盖飞书群入口,避免转给 manager 触发模型。
    # 共享模块 scripts/slash_commands.py,行为与 hook 一致。
    # 命中命令: /help /team /usage /tmux /send /compact <agent> /compact-all
    matched, reply = slash_commands.dispatch(text)
    if matched:
        ts = time.strftime('%Y-%m-%d %H:%M:%S')
        first = text.strip().split()[0] if text.strip() else ""
        line = f"[{ts}] slash {first} msg_id={msg_id} → 群聊回显(无 agent 介入)"
        print(line)
        try:
            with open(os.path.join(os.path.dirname(__file__),
                                   ".tmux_intercept.log"), "a") as f:
                f.write(line + "\n")
        except Exception:
            pass
        try:
            from feishu_msg import _lark_im_send, CHAT, post_system_to_group
            chat_id = CHAT()
            if not chat_id:
                print(f"  ⚠️ chat_id 未配置,无法回显")
            elif isinstance(reply, dict) and reply.get("card"):
                # 自定义卡片(/team /usage)
                _lark_im_send(chat_id, card=reply["card"])
            else:
                # 文本回显 → 包进「系统消息」卡片,避免 bot · ? 难看标签
                body = reply if isinstance(reply, str) else (
                    reply.get("text", "(空)") if isinstance(reply, dict) else "(空)")
                if first == "/tmux":
                    body = f"```\n{body}\n```"
                post_system_to_group(body)
        except Exception as e:
            print(f"  ⚠️ slash 回显失败: {e}")
        _advance_cursor()
        return

    print(f"[{time.strftime('%H:%M:%S')}] 新消息: {text[:500]}")

    # /tmux 本地拦截 — 不路由任何 agent,不走 LLM。
    # 飞书入口补丁：.claude/hooks 里的 tmux_intercept.py 只覆盖 Claude Code 本机
    # 输入,群消息会被默认派给 manager 变相触发模型。两处入口共享 tmux_command
    # 模块,行为一致：/tmux→manager 10 / /tmux <agent> / /tmux <agent> <lines>。
    tmux_args = tmux_command.parse(text)
    if tmux_args:
        t_agent, t_lines = tmux_args
        ts = time.strftime('%Y-%m-%d %H:%M:%S')
        line = f"[{ts}] /tmux {t_agent} {t_lines} msg_id={msg_id} → 群聊回显(无 agent 介入)"
        print(line)
        try:
            with open(os.path.join(os.path.dirname(__file__),
                                   ".tmux_intercept.log"), "a") as f:
                f.write(line + "\n")
        except Exception:
            pass
        body = tmux_command.capture(TMUX_SESSION, t_agent, t_lines)
        try:
            cmd_say("tmux", f"```\n{body}\n```")
        except Exception as e:
            print(f"  \u26a0\ufe0f /tmux 回显失败: {e}")
        _advance_cursor()
        return

    # /team 本地拦截 — 零 LLM,采集团队状态回群
    if team_command.parse(text):
        ts = time.strftime('%Y-%m-%d %H:%M:%S')
        print(f"[{ts}] /team msg_id={msg_id} → 群聊回显(无 agent 介入)")
        body = team_command.collect_and_format(TMUX_SESSION)
        try:
            cmd_say("team", f"```\n{body}\n```")
        except Exception as e:
            print(f"  \u26a0\ufe0f /team 回显失败: {e}")
        _advance_cursor()
        return

    # 图片处理
    image_key = event.get("image_key", "")
    if image_key and msg_id:
        def _download_async():
            path = download_image(msg_id, image_key)
            if path:
                target = _state.parse_targets(text)
                agent = target[0] if target else "manager"
                notify = TPL_IMAGE_DOWNLOADED.format(path=path)
                enqueue_message(agent, notify, f"{msg_id}_img", is_user_msg=True)
        threading.Thread(target=_download_async, daemon=True).start()

    sender_agent = _state.parse_sender(text)
    targets = _state.parse_targets(text)
    is_user_event = event.get("sender_type", "user") == "user"

    if targets:
        for target in targets:
            if target == sender_agent:
                continue
            print(f"  路由: @{target} ← {sender_agent or '用户'}")
            wake_agent(target, text, sender_agent=sender_agent,
                       full_text=text, msg_id=msg_id)
    elif is_user_event:
        # 真实用户消息即使正文包含 "【manager " / "【toolsmith ·" 这类
        # 业务文本,也必须默认路由 manager,不能被 parse_sender() 误判吞掉。
        wake_agent("manager", text, msg_id=msg_id, full_text=text)

    # 推进 cursor — 必须放在成功路由之后,否则半路异常会让 cursor 跳过
    # 没处理完的消息。watchdog 只看 mtime,不看 content,所以这里的写入也
    # 顺带刷新了心跳。
    _advance_cursor()

# ── 事件心跳 + 重启补抓 ────────────────────────────────────────
# 设计意图:
#   Feishu WebSocket 长连接在 Docker bridge + 云 NAT 环境下,如果长时间没
#   流量,中间层的 conntrack 表项过期,连接被静默切断。客户端 TCP read 永远
#   阻塞 — 进程看似活着(PID 存在),事件流其实已经死了。watchdog 默认只检查
#   PID 活性,查不出来。
#
#   双层防御:
#   1) 每次从 WebSocket 读到事件(即使被 chat_id 或 bot 自发消息过滤掉)就
#      刷新 CURSOR_FILE 的 mtime 当心跳。scripts/watchdog.py 配 health_file
#      指向 CURSOR_FILE,长时间没更新就判定 router 已死并自动重启。
#   2) 每次成功把一条用户消息路由到 agent,就把墙钟时间写进 CURSOR_FILE 的
#      内容(这叫 cursor 推进)。router 启动时(首次或被重启)先读这个 cursor,
#      调 `im +chat-messages-list --start $cursor --sort asc` 把断联期间
#      错过的群聊消息按时间序拉回来,逐条走 handle_event(),然后才接
#      WebSocket 事件循环。
#
#   为什么一个文件兼做两件事: content 代表"最后成功路由的本团队消息时间",
#   mtime 代表"最后从 WebSocket 收到任何事件的时间"。两个维度刚好需要独立,
#   cursor 写操作天然会更新 mtime(心跳也顺带刷新),cross-team 事件只需要
#   os.utime 就够了(不能动 cursor content,否则会漏补抓本团队的消息)。

CURSOR_FILE = os.path.join(PID_DIR, ".router.cursor")


def _refresh_heartbeat():
    """更新 CURSOR_FILE 的 mtime(不动 content)。任何从 WebSocket 收到的
    事件都应该调一次,包括后续会被过滤掉的 cross-team 事件和 bot 自发消息。
    """
    try:
        os.utime(CURSOR_FILE, None)
    except FileNotFoundError:
        # 首次启动,CURSOR_FILE 还没创建。用当前时间初始化一个,这样 mtime
        # 立刻有合法值,content 也有合法 cursor(假装"从现在开始是新的
        # 世界"); 下次重启时不会因为 cursor 缺失而 skip 补抓。
        _advance_cursor()


def _advance_cursor():
    """把当前墙钟写进 CURSOR_FILE 的 content。在成功路由一条本团队用户消息
    之后调用。同时会刷新 mtime(心跳顺带)。
    """
    _advance_cursor_to(time.time())


def _advance_cursor_to(ts):
    """把 cursor 推进到 ts(unix 秒)。只在 ts > 当前 cursor 时写入(单调递增)。"""
    current = _load_cursor()
    if current is not None and ts <= current:
        return
    try:
        with open(CURSOR_FILE, "w") as f:
            f.write(f"{ts:.3f}")
    except Exception as e:
        print(f"  ⚠️ 写 cursor 失败: {e}")


def _load_cursor():
    """读 CURSOR_FILE 的 content(unix 秒,float)。首次启动或空文件返回 None。"""
    try:
        with open(CURSOR_FILE) as f:
            content = f.read().strip()
        return float(content) if content else None
    except (FileNotFoundError, ValueError):
        return None


def _catchup_from_history(chat_id):
    """根据 cursor 从聊天记录 API 补抓错过的用户消息,逐条走 handle_event。

    首次启动(无 cursor)写入当前时间并跳过;硬 30 秒上限,防止 cursor 极久远
    时卡死 WebSocket 启动。
    """
    from datetime import datetime
    cursor = _load_cursor()
    if cursor is None:
        _advance_cursor()
        print("📥 首次启动,无 cursor,跳过历史补抓")
        return 0

    # 向前退 1 秒避免秒级精度漏掉同一秒的消息。代价: 上一条消息会被 replay
    # 一次(跨 session seen_ids 清空),manager 可能重复响应一次。相比丢消息,
    # 偶尔重复是更可接受的代价。
    start_dt = datetime.fromtimestamp(cursor - 1).astimezone()
    start_iso = start_dt.isoformat(timespec="seconds")
    print(f"📥 历史补抓: 从 {start_iso} 开始拉错过的群聊消息")

    fetched = replayed = 0
    page_token = ""
    deadline = time.time() + 30  # 硬上限,避免 catchup 阻塞 WebSocket 启动太久
    while time.time() < deadline:
        args = ["im", "+chat-messages-list",
                "--chat-id", chat_id,
                "--start", start_iso,
                "--sort", "asc",
                "--page-size", "50",
                "--as", "bot",
                "--format", "json"]
        if page_token:
            args += ["--page-token", page_token]
        try:
            data = _lark_run(args, timeout=40)
        except subprocess.TimeoutExpired:
            print("  ⚠️ 历史补抓超时,放弃剩余页")
            break
        if data is None:
            # ADR silent_swallow_remaining P1 ⑥: 格式对齐 _check_lark_result
            # 的 '⚠️ lark-cli 调用失败: <action>' grep pattern,方便未来统一
            # 监控抓吞错点。逻辑保留原样: catchup 是 best-effort,单页失败不
            # 致命,break 让调用方从上次 cursor 重试。
            print(f"  ⚠️ lark-cli 调用失败: 历史补抓 {chat_id} (停止本轮)",
                  file=sys.stderr)
            break
        max_create_time = None
        for m in data.get("messages", data.get("items", [])):
            fetched += 1
            # 记录本页最新消息时间,用于独立推进 cursor
            ct = m.get("create_time")
            if ct:
                try:
                    ct_str = str(ct).strip()
                    # lark-cli +chat-messages-list 返回格式化时间 "2026-04-20 09:26"
                    # 标准 API 返回 unix ms "1776591454415" 或 unix s "1776591454.415"
                    if re.match(r"\d{4}-\d{2}-\d{2}", ct_str):
                        from datetime import datetime as _dt
                        ct_f = _dt.strptime(ct_str, "%Y-%m-%d %H:%M").timestamp()
                    elif "." in ct_str:
                        ct_f = float(ct_str)
                    else:
                        v = float(ct_str)
                        ct_f = v / 1000 if v > 1e12 else v
                    if max_create_time is None or ct_f > max_create_time:
                        max_create_time = ct_f
                except (ValueError, TypeError):
                    pass
            sender = m.get("sender", {})
            # 只 replay 真实用户消息,跳过 app/bot 发的卡片(watchdog 告警、
            # manager 历史回复等),避免这些被 replay 成"新消息"再触发一轮处理
            if sender.get("sender_type") != "user":
                continue
            # 非文本消息(图片/文件/post)暂时跳过,避免二次下载和卡片解析
            if m.get("msg_type") != "text":
                print(f"  ⏭  跳过非文本历史消息: {m.get('message_id')}")
                continue
            # content 字段兼容:顶层 content 或 body.content(JSON 编码)
            raw_content = m.get("content") or m.get("body", {}).get("content", "")
            try:
                text = json.loads(raw_content).get("text", "") if raw_content else ""
            except (json.JSONDecodeError, AttributeError, TypeError):
                text = raw_content or ""
            event = {
                "message_id":  m.get("message_id", ""),
                "chat_id":     chat_id,
                "sender_id":   sender.get("id", ""),
                "text":        text,
                "message_type": "text",
            }
            try:
                handle_event(event)
                replayed += 1
            except Exception as e:
                print(f"  ⚠️ replay 事件失败: {e}")
        # 独立推进 cursor:不管 handle_event 成败,只要拉到了消息就推进,
        # 打破 dedup-cursor 死锁(seen_ids 锁住 msg_id 但 cursor 不前进)
        if max_create_time is not None:
            _advance_cursor_to(max_create_time)
        if not data.get("has_more"):
            break
        page_token = data.get("page_token", "")
        if not page_token:
            break

    if time.time() >= deadline:
        print("  ⚠️ 历史补抓达到 30s 硬上限,剩余页放弃(下次重启会继续)")
    print(f"📥 历史补抓完成: 拉取 {fetched} 条, replay {replayed} 条到 agent")
    return replayed


# ── PID 锁 ──────────────────────────────────────────────────

PID_FILE = os.path.join(PID_DIR, ".router.pid")

def acquire_pid_lock():
    if os.path.exists(PID_FILE):
        try:
            with open(PID_FILE) as f:
                old_pid = int(f.read().strip())
            os.kill(old_pid, 0)
            try:
                with open(f"/proc/{old_pid}/cmdline", "rb") as f:
                    cmdline = f.read().decode("utf-8", errors="ignore")
                if "feishu_router" not in cmdline:
                    raise OSError("PID reuse: not router")
            except (FileNotFoundError, PermissionError):
                raise OSError("proc gone or no /proc")
            print(f"❌ Router 已在运行 (PID {old_pid})，请勿重复启动")
            sys.exit(1)
        except (ValueError, OSError):
            pass
    with open(PID_FILE, "w") as f:
        f.write(str(os.getpid()))
    atexit.register(_cleanup_pid)

def _cleanup_pid():
    try:
        if os.path.exists(PID_FILE):
            with open(PID_FILE) as f:
                pid = int(f.read().strip())
            if pid == os.getpid():
                os.remove(PID_FILE)
    except Exception:
        pass

signal.signal(signal.SIGTERM, lambda *_: sys.exit(0))

# ── 队列投递后台线程 ─────────────────────────────────────────

def _queue_delivery_loop():
    """后台线程：定期尝试投递所有 agent 的待处理消息。"""
    last_unread_check = 0
    while True:
        try:
            for agent_name in _state.reload_agents():
                dequeue_pending(agent_name)
            last_unread_check = check_manager_unread(last_unread_check)
        except Exception as e:
            print(f"  ⚠️ 队列投递异常: {e}")
        time.sleep(3)

# ── 主函数 ───────────────────────────────────────────────────

def main():
    print("🚀 Router Daemon 启动")
    acquire_pid_lock()

    # 给新进程一个 grace period: 立刻刷新心跳文件 mtime,避免 WebSocket 静默
    # 期(群聊冷清)时新 router 启动后 60s 内没收到事件就被 watchdog 二次误判
    # 重启,形成连锁。_refresh_heartbeat 会在文件缺失时自动 _advance_cursor 初
    # 始化,不会漏补抓。
    _refresh_heartbeat()

    cfg = load_runtime_config()
    chat_id = cfg.get("chat_id", "")
    if not chat_id:
        print("❌ chat_id 未配置，请先运行 setup.py")
        sys.exit(1)

    _state.chat_id = chat_id  # 启用跨团队事件过滤
    _state.init_bot_id()
    print(f"💬 监听群组: {chat_id}")
    print(f"👥 Agent 列表: {', '.join(_state.reload_agents())}")

    # 启动队列投递后台线程
    delivery_thread = threading.Thread(target=_queue_delivery_loop, daemon=True)
    delivery_thread.start()

    # 启动时把断联期间错过的群聊消息补抓回来(见 CURSOR_FILE 那段的说明)。
    # 跑在 daemon 线程里,不阻塞主线程立刻进 WebSocket 事件循环 —— 如果
    # 同步跑,最坏情况 catchup 每页 40s 超时累加可以让新 WebSocket 启动
    # 推迟到 800 秒之后,期间 bash pipeline 的 stdin buffer(~64KB)会被
    # 新流入的事件写爆,造成二次丢失。daemon 线程让 catchup 和实时流
    # 并行跑,重复消息由 _state.seen_ids 去重。
    #
    # 额外的轮询模式(ClaudeTeam shared-profile 容器部署专用):
    # 如果 App 服务端没订阅 im.message.receive_v1 事件(这是共享宿主机
    # profile 最常见的症状), WebSocket 永远收不到事件。这里开一个后台线程
    # 每 5 秒再跑一次 catchup,相当于把 WebSocket 退化成 HTTP 轮询。
    # seen_ids 保证重复消息不会被路由两次, cursor 保证只拉新消息,所以
    # 即使 WebSocket 后来恢复也不会冲突。代价是延迟 ≤ 5s、chat-messages-list
    # 调用频率上升。
    def _poll_catchup_loop():
        cumulative_replayed = 0
        last_replay_time = time.time()
        warned = False
        while True:
            try:
                n = _catchup_from_history(chat_id) or 0
                cumulative_replayed += n
                if n > 0:
                    last_replay_time = time.time()
                    warned = False
                # 心跳: 证明 poll 线程活着(watchdog 需要)
                _refresh_heartbeat()
                # 5 分钟内 0 replay → 可能 WebSocket 已断链
                if (not warned
                        and time.time() - last_replay_time > 300
                        and cumulative_replayed == 0):
                    print("=" * 60)
                    print("⚠️  轮询 5 分钟内未 replay 任何消息")
                    print("   如群内有新消息但 agent 无反应,")
                    print("   WebSocket 可能已断链,catchup 正在兜底。")
                    print("=" * 60)
                    warned = True
            except Exception as e:
                print(f"  ⚠️ 轮询 catchup 异常: {e}")
            time.sleep(5)
    threading.Thread(target=_poll_catchup_loop, daemon=True).start()

    # Bug 16 防御:启动后 45 秒内如果一条事件都没到,打印醒目警告。
    # 最常见的根因是 App 的 im.message.receive_v1 事件订阅没配(config init
    # 用 --app-id/--app-secret-stdin 只存凭证,不会调 Feishu API 订阅事件)。
    # 这种情况下 WebSocket 连接正常、--as bot 正常,但服务器永远不推事件,
    # 用户看来就是"发消息没反应"。
    def _event_watchdog():
        time.sleep(45)
        if _state.first_event_at is None:
            print("=" * 60)
            print("🚨 Router 启动 45 秒内未收到任何事件!")
            print("   最可能的根因: App 未订阅 im.message.receive_v1 事件")
            print("   修复方法:")
            print("     npx @larksuite/cli config init --new")
            print("     ↳ 扫码 → 选「使用已有应用」→ 选当前 App ID")
            print("   这会把事件订阅推到 App 服务端并自动发布。")
            print("=" * 60)
    threading.Thread(target=_event_watchdog, daemon=True).start()

    stdin_mode = "--stdin" in sys.argv

    if stdin_mode:
        # 管道模式：从 stdin 读取 lark-cli event 的 NDJSON 流
        print("📡 模式: stdin 事件流（lark-cli event +subscribe）")
        print("=" * 50)
        for line in sys.stdin:
            line = line.strip()
            if not line:
                continue
            try:
                event = json.loads(line)
                handle_event(event)
            except json.JSONDecodeError:
                print(f"  ⚠️ 无法解析事件: {line[:100]}")
            except Exception as e:
                print(f"  ⚠️ 事件处理异常: {e}")
    else:
        # 自启模式：自动启动 lark-cli event 子进程
        print("📡 模式: 自启 lark-cli event +subscribe")
        print("=" * 50)
        proc = subprocess.Popen(
            LARK_CLI + ["event", "+subscribe",
                        "--event-types", "im.message.receive_v1",
                        "--compact", "--quiet", "--force", "--as", "bot"],
            stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
        try:
            for line in proc.stdout:
                line = line.strip()
                if not line:
                    continue
                try:
                    event = json.loads(line)
                    handle_event(event)
                except json.JSONDecodeError:
                    print(f"  ⚠️ 无法解析事件: {line[:100]}")
                except Exception as e:
                    print(f"  ⚠️ 事件处理异常: {e}")
        except KeyboardInterrupt:
            proc.terminate()
        finally:
            proc.terminate()

if __name__ == "__main__":
    main()
