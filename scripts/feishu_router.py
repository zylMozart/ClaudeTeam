#!/usr/bin/env python3
"""
Router Daemon — 从 lark-cli event 事件流读取消息，路由到 tmux 窗口

用法（管道模式）：
  lark-cli event +subscribe --event-types im.message.receive_v1 --compact --quiet --force \
    | python3 scripts/feishu_router.py --stdin

也可独立运行（兼容旧模式，自启 lark-cli 子进程）：
  python3 scripts/feishu_router.py
"""
import sys, os, json, time, re, subprocess, atexit, signal, threading

sys.path.insert(0, os.path.dirname(__file__))
from config import AGENTS, TMUX_SESSION, PROJECT_ROOT, load_runtime_config
from tmux_utils import inject_when_idle, is_agent_idle
from msg_queue import enqueue_message, has_pending_messages, dequeue_pending, check_manager_unread

IMAGES_DIR = os.path.join(PROJECT_ROOT, "workspace", "shared", "images")
LARK_CLI = ["npx", "@larksuite/cli"]

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


class RouterState:
    """封装 Router 的可变状态。"""

    def __init__(self):
        self.bot_open_id = ""
        self._team_mtime = 0
        self._agent_names = []
        self.seen_ids = set()

    def init_bot_id(self):
        """通过 lark-cli 获取 bot 的 open_id（从群成员列表中识别 bot 成员）。"""
        try:
            cfg_path = os.path.join(os.path.dirname(__file__), "runtime_config.json")
            with open(cfg_path) as f:
                chat_id = json.load(f).get("chat_id", "")
            if not chat_id:
                print("⚠️ 无 chat_id，自回声过滤将不可用")
                return
            r = subprocess.run(
                LARK_CLI + ["im", "chat.members", "get",
                            "--params", json.dumps({"chat_id": chat_id, "member_id_type": "open_id"}),
                            "--as", "bot", "--page-all", "--format", "json"],
                capture_output=True, text=True, timeout=15)
            if r.returncode == 0:
                d = json.loads(r.stdout)
                items = d.get("data", {}).get("items", [])
                # Bot members have member_id_type "open_id" but no tenant_key,
                # or we can identify by checking all members — the bot is the one
                # whose open_id starts with "ou_" and is not a regular user.
                # Fallback: use the app_id prefix to derive bot identity.
                for item in items:
                    if item.get("member_id_type") == "open_id" and not item.get("tenant_key"):
                        self.bot_open_id = item["member_id"]
                        print(f"🤖 Bot open_id: {self.bot_open_id}")
                        return
                print("⚠️ 未在群成员中找到 bot，自回声过滤将不可用")
            else:
                print(f"⚠️ 获取群成员失败，自回声过滤将不可用")
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

# ── 触发 tmux 窗口 ────────────────────────────────────────────

def wake_agent(agent_name, message_preview, sender_agent=None,
               full_text=None, msg_id=""):
    """向 agent 投递消息：先尝试直接投递，忙碌则入队"""
    if sender_agent:
        prompt = TPL_AGENT_NOTIFY.format(
            sender=sender_agent, agent=agent_name,
            preview=message_preview[:500])
    else:
        content = full_text or message_preview
        if len(content) > 400:
            msg_file = os.path.join(PROJECT_ROOT, "workspace", "shared",
                                    f".router_msg_{agent_name}.txt")
            os.makedirs(os.path.dirname(msg_file), exist_ok=True)
            with open(msg_file, "w", encoding="utf-8") as f:
                f.write(content)
            prompt = TPL_USER_MSG_LONG.format(
                file_path=msg_file, preview=content[:200], agent=agent_name)
        else:
            prompt = TPL_USER_MSG_SHORT.format(
                content=content, agent=agent_name)

    is_user_msg = (sender_agent is None)
    has_pending = has_pending_messages(agent_name)

    if not has_pending and is_agent_idle(TMUX_SESSION, agent_name):
        ok = inject_when_idle(TMUX_SESSION, agent_name, prompt,
                              wait_secs=2, force_after_wait=False)
        if ok:
            print(f"  → 已触发 {agent_name} 窗口（直接投递）")
            return

    enqueue_message(agent_name, prompt, msg_id, is_user_msg=is_user_msg)
    if has_pending:
        print(f"  📥 消息已入队 {agent_name}（队列有积压，保证 FIFO）")
    else:
        print(f"  📥 消息已入队 {agent_name}（agent 忙碌，等待投递）")

# ── 处理单条事件 ──────────────────────────────────────────────

def handle_event(event):
    """处理一条 --compact 格式的事件 JSON。"""
    msg_id = event.get("message_id", "")
    if not msg_id or msg_id in _state.seen_ids:
        return
    _state.seen_ids.add(msg_id)

    # 过滤 bot 自己的消息
    sender_id = event.get("sender_id", "")
    if _state.is_bot_message(sender_id):
        return

    # --compact 模式下 text 字段已解析好
    text = event.get("text", event.get("content", ""))
    msg_type = event.get("message_type", "text")

    if not text:
        return

    print(f"[{time.strftime('%H:%M:%S')}] 新消息: {text[:500]}")

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

    if targets:
        for target in targets:
            if target == sender_agent:
                continue
            print(f"  路由: @{target} ← {sender_agent or '用户'}")
            wake_agent(target, text, sender_agent=sender_agent,
                       full_text=text, msg_id=msg_id)
    elif not sender_agent:
        # 用户消息默认路由到 manager
        wake_agent("manager", text, msg_id=msg_id, full_text=text)

# ── PID 锁 ──────────────────────────────────────────────────

PID_FILE = os.path.join(os.path.dirname(__file__), ".router.pid")

def acquire_pid_lock():
    if os.path.exists(PID_FILE):
        try:
            with open(PID_FILE) as f:
                old_pid = int(f.read().strip())
            os.kill(old_pid, 0)
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

    cfg = load_runtime_config()
    chat_id = cfg.get("chat_id", "")
    if not chat_id:
        print("❌ chat_id 未配置，请先运行 setup.py")
        sys.exit(1)

    _state.init_bot_id()
    print(f"💬 监听群组: {chat_id}")
    print(f"👥 Agent 列表: {', '.join(_state.reload_agents())}")

    # 启动队列投递后台线程
    delivery_thread = threading.Thread(target=_queue_delivery_loop, daemon=True)
    delivery_thread.start()

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
                        "--compact", "--quiet", "--force"],
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
