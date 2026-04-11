#!/usr/bin/env python3
"""
Router Daemon — 轮询飞书群组消息，解析 @mention，路由到对应 tmux 窗口
运行：python3 scripts/feishu_router.py
"""
import sys, os, json, time, re, subprocess, requests, atexit, signal
from concurrent.futures import ThreadPoolExecutor

sys.path.insert(0, os.path.dirname(__file__))
from config import BASE, AGENTS, TMUX_SESSION, ROUTER_POLL_INTERVAL, PROJECT_ROOT, load_runtime_config
from tmux_utils import inject_when_idle, is_agent_idle
from feishu_api import get_token, h, invalidate_token as _invalidate_token
from msg_parser import parse_message
from msg_queue import enqueue_message, has_pending_messages, dequeue_pending, check_manager_unread

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

TPL_IMAGE_RICH_TEXT = (
    "【群聊消息】用户在群里发送了图片/富文本消息:\n{content}\n\n"
    "你可以使用 Read 工具读取图片。处理完成后用以下命令回复群里:\n"
    "python3 scripts/feishu_msg.py say manager \"<你的回复>\""
)

TPL_IMAGE_DOWNLOADED = (
    "【Router 补充】之前的图片已下载完成。\n"
    "本地路径: {path}\n"
    "你可以使用 Read 工具查看图片。"
)

TPL_COMBINED_MSGS = (
    "【群聊消息】用户连续发了 {count} 条消息:\n\n"
    "{messages}"
    "请逐条处理，然后用以下命令回复群里:\n"
    "python3 scripts/feishu_msg.py say {agent} \"<你的回复>\""
)

load_cfg = load_runtime_config

# ── Router 状态管理 ──────────────────────────────────────────

_SEEN_IDS_FILE = os.path.join(os.path.dirname(__file__), ".router_seen_ids.json")
_TEAM_FILE = os.path.join(PROJECT_ROOT, "team.json")


class RouterState:
    """封装 Router 的全局可变状态，避免裸全局变量。"""

    def __init__(self):
        self.bot_open_id = ""
        self._team_mtime = 0
        self._agent_names = []
        self.seen_ids = set()

    def init_bot_id(self):
        """启动时调用 /bot/v3/info 获取 bot 自身的 open_id。"""
        token = get_token()
        r = requests.get(f"{BASE}/bot/v3/info", headers=h(token))
        if r.status_code == 200:
            data = r.json().get("bot", {})
            self.bot_open_id = data.get("open_id", "")
            print(f"🤖 Bot open_id: {self.bot_open_id}")
        else:
            print(f"⚠️ 获取 bot info 失败: HTTP {r.status_code}, 自回声过滤将不可用")

    def load_seen(self):
        """从文件加载已处理消息 ID。"""
        if os.path.exists(_SEEN_IDS_FILE):
            with open(_SEEN_IDS_FILE) as f:
                self.seen_ids = set(json.load(f))
        return self.seen_ids

    def save_seen(self):
        """持久化已处理消息 ID（保留最近 500 条）。"""
        lst = list(self.seen_ids)[-500:]
        with open(_SEEN_IDS_FILE, "w") as f:
            json.dump(lst, f)

    def reload_agents(self):
        """检查 team.json 变更，热加载 agent 列表。"""
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
        """检查消息是否来自 bot 自身。"""
        return bool(self.bot_open_id and sender_id == self.bot_open_id)

    def parse_targets(self, text):
        """从消息文本中提取被 @mention 的 agent 名称列表。"""
        found = []
        for name in self.reload_agents():
            if f"@{name}" in text:
                found.append(name)
        return found

    def parse_sender(self, text):
        """从消息格式 【agent · role】 中提取发件人。"""
        m = re.search(r"【(\w[\w-]*)[\s·]", text)
        if m:
            name = m.group(1)
            if name in self.reload_agents():
                return name
        return None


# 模块级单例
_state = RouterState()

# ── 触发 tmux 窗口 ────────────────────────────────────────────

def wake_agent(agent_name, message_preview, sender_agent=None,
               full_text=None, msg_id=""):
    """向 agent 投递消息：先尝试直接投递，忙碌则入队"""
    # 构建 prompt
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

    # 检查该 agent 的待投递队列是否有积压消息
    has_pending = has_pending_messages(agent_name)

    # 仅当队列为空且 agent 空闲时，才可以直接投递（保证 FIFO）
    if not has_pending and is_agent_idle(TMUX_SESSION, agent_name):
        ok = inject_when_idle(TMUX_SESSION, agent_name, prompt,
                              wait_secs=2, force_after_wait=False)
        if ok:
            print(f"  → 已触发 {agent_name} 窗口（直接投递）")
            return

    # 队列有积压 或 agent 忙碌 → 入队等待（保证 FIFO 顺序）
    enqueue_message(agent_name, prompt, msg_id, is_user_msg=is_user_msg)
    if has_pending:
        print(f"  📥 消息已入队 {agent_name}（队列有积压，保证 FIFO）")
    else:
        print(f"  📥 消息已入队 {agent_name}（agent 忙碌，等待投递）")

# ── 图片下载 ─────────────────────────────────────────────────

def download_image(token, message_id, image_key, create_time):
    """下载飞书图片到本地，返回绝对路径；失败返回 None。"""
    url = f"{BASE}/im/v1/messages/{message_id}/resources/{image_key}?type=image"
    r = requests.get(url, headers={"Authorization": f"Bearer {token}"}, stream=True)
    if r.status_code != 200:
        print(f"  ⚠️  图片下载失败: HTTP {r.status_code}")
        return None

    ct = r.headers.get("Content-Type", "image/jpeg")
    ext_map = {"image/jpeg": ".jpg", "image/png": ".png",
               "image/gif": ".gif", "image/webp": ".webp"}
    ext = ext_map.get(ct.split(";")[0].strip(), ".bin")

    ts_str = time.strftime("%Y%m%d_%H%M%S", time.localtime(int(create_time)))
    filename = f"{ts_str}_{message_id[:8]}{ext}"
    os.makedirs(IMAGES_DIR, exist_ok=True)
    filepath = os.path.join(IMAGES_DIR, filename)

    with open(filepath, "wb") as f:
        for chunk in r.iter_content(8192):
            f.write(chunk)

    print(f"  📥 图片已保存: {filepath}")
    return filepath

# ── 图片下载异步化（方案 C）───────────────────────────────────

_download_pool = ThreadPoolExecutor(max_workers=2)

def _on_image_downloaded(future, agent_name, msg_id):
    """图片下载完成回调，追加通知给 agent"""
    try:
        local_path = future.result()
        if local_path:
            notify = TPL_IMAGE_DOWNLOADED.format(path=local_path)
            enqueue_message(agent_name, notify, f"{msg_id}_img", is_user_msg=True)
    except Exception as e:
        print(f"  ⚠️ 图片下载回调异常: {e}")

# ── 轮询群消息 ────────────────────────────────────────────────

def poll_messages(token, chat_id, since_ts_ms):
    """拉取群组自 since_ts_ms 以来的新消息"""
    params = {
        "container_id_type": "chat",
        "container_id": chat_id,
        "sort_type": "ByCreateTimeAsc",
        "page_size": 20,
        "start_time": str(int(since_ts_ms // 1000)),  # 秒
    }
    r = requests.get(f"{BASE}/im/v1/messages", headers=h(token), params=params)
    d = r.json()
    if d.get("code") != 0:
        msg = d.get('msg', '')
        if 'access token' in msg.lower() or d.get("code") == 99991663:
            print(f"  🔄 Token 失效，清除缓存并重试...")
            _invalidate_token()
            token = get_token()
            r = requests.get(f"{BASE}/im/v1/messages", headers=h(token), params=params)
            d = r.json()
            if d.get("code") == 0:
                return d.get("data", {}).get("items", [])
        print(f"  ⚠️  拉取消息失败: {msg}")
        return []
    return d.get("data", {}).get("items", [])

# ── PID 锁文件（防止重复启动）────────────────────────────────────

PID_FILE = os.path.join(os.path.dirname(__file__), ".router.pid")

def acquire_pid_lock():
    """获取 PID 锁，如果已有活着的实例则退出"""
    if os.path.exists(PID_FILE):
        try:
            with open(PID_FILE) as f:
                old_pid = int(f.read().strip())
            os.kill(old_pid, 0)  # 检测进程是否存活（不发送信号）
            print(f"❌ Router 已在运行 (PID {old_pid})，请勿重复启动")
            sys.exit(1)
        except (ValueError, OSError):
            # PID 文件损坏或进程已不存在，清理旧文件继续启动
            pass

    with open(PID_FILE, "w") as f:
        f.write(str(os.getpid()))
    atexit.register(_cleanup_pid)

def _cleanup_pid():
    """退出时清理 PID 文件"""
    try:
        if os.path.exists(PID_FILE):
            with open(PID_FILE) as f:
                pid = int(f.read().strip())
            if pid == os.getpid():
                os.remove(PID_FILE)
    except Exception:
        pass

# SIGTERM 也触发正常退出（atexit 会被调用）
signal.signal(signal.SIGTERM, lambda *_: sys.exit(0))

# ── 主循环 ────────────────────────────────────────────────────

def main():
    print("🚀 Router Daemon 启动")
    acquire_pid_lock()
    _state.init_bot_id()
    cfg = load_cfg()
    chat_id = cfg.get("chat_id", "")
    if not chat_id:
        print("❌ chat_id 未配置，请先运行 setup.py")
        sys.exit(1)

    _state.load_seen()
    since_ts = int(time.time() * 1000)
    last_unread_check = 0

    print(f"💬 监听群组: {chat_id}")
    print(f"🔄 轮询间隔: {ROUTER_POLL_INTERVAL}s")
    print(f"👥 Agent 列表: {', '.join(_state.reload_agents())}")
    print("=" * 50)

    while True:
        try:
            token = get_token()
            msgs = poll_messages(token, chat_id, since_ts)

            # 方案 B：收集同一 agent 的用户消息用于合并
            pending_user_msgs = {}  # agent_name -> [{"text", "msg_id", "full_text"}]
            # 方案 C：收集图片下载 future，路由确定后绑定目标 agent
            _img_futures = []

            for msg in msgs:
                msg_id  = msg.get("message_id", "")
                if msg_id in _state.seen_ids:
                    continue
                _state.seen_ids.add(msg_id)

                # 过滤 bot 自己发的消息（防止自回声）
                msg_sender_id = msg.get("sender", {}).get("id", "")
                if _state.is_bot_message(msg_sender_id):
                    print(f"  ⏭️  跳过 bot 自己的消息: {msg_id}")
                    continue

                # 更新时间窗口（用当前本地时间，避免 since_ts 超过服务器时间）
                create_ts = int(msg.get("create_time", "0"))
                if create_ts * 1000 > since_ts:
                    since_ts = int(time.time() * 1000)

                # 解析消息内容
                parsed = parse_message(msg)
                if parsed["skipped"]:
                    print(f"  ⏭️  跳过不支持的消息类型: {parsed['msg_type']}")
                    continue
                text = parsed["text"]
                msg_type = parsed["msg_type"]
                create_time = msg.get("create_time", str(int(time.time())))

                # 方案 C：图片异步下载
                for image_key in parsed["image_keys"]:
                    _img_futures.append({
                        "future": _download_pool.submit(
                            download_image, token, msg_id, image_key, create_time),
                        "msg_id": msg_id,
                    })

                sender_id = msg.get("sender", {}).get("id", "")
                print(f"[{time.strftime('%H:%M:%S')}] 新消息: {text[:10000]}")

                sender_agent = _state.parse_sender(text)
                targets = _state.parse_targets(text)

                # 图片/富文本消息生成更具体的 full_text 提示
                if msg_type in ("image", "post") and not sender_agent:
                    has_images = "图片路径:" in text or "image_key:" in text
                    if has_images:
                        full_text_override = TPL_IMAGE_RICH_TEXT.format(content=text)
                    else:
                        full_text_override = text
                else:
                    full_text_override = text

                # 确定路由目标
                routed_to = None
                if targets:
                    for target in targets:
                        if target == sender_agent:
                            continue
                        print(f"  路由: @{target} ← {sender_agent or '用户'}")
                        wake_agent(target, text, sender_agent=sender_agent,
                                   full_text=full_text_override, msg_id=msg_id)
                        routed_to = target
                elif not sender_agent:
                    # 方案 B：用户消息收集，同一 agent 的合并
                    routed_to = "manager"
                    if routed_to not in pending_user_msgs:
                        pending_user_msgs[routed_to] = []
                    pending_user_msgs[routed_to].append({
                        "text": text, "msg_id": msg_id,
                        "full_text": full_text_override
                    })

                # 方案 C：为本条消息的图片下载绑定回调到实际路由目标
                actual_target = routed_to or "manager"
                for img_info in _img_futures:
                    if img_info["msg_id"] == msg_id:
                        img_info["future"].add_done_callback(
                            lambda f, aid=actual_target, mid=msg_id:
                                _on_image_downloaded(f, aid, mid))
                _img_futures = [i for i in _img_futures if i["msg_id"] != msg_id]

            # 方案 B：合并投递用户消息
            for agent_name, user_msgs in pending_user_msgs.items():
                if len(user_msgs) == 1:
                    wake_agent(agent_name, user_msgs[0]["text"],
                               msg_id=user_msgs[0]["msg_id"],
                               full_text=user_msgs[0]["full_text"])
                else:
                    msg_parts = ""
                    for i, um in enumerate(user_msgs, 1):
                        content = um["full_text"] or um["text"]
                        msg_parts += f"--- 第 {i} 条 ---\n{content}\n\n"
                    combined = TPL_COMBINED_MSGS.format(
                        count=len(user_msgs), messages=msg_parts, agent=agent_name)
                    wake_agent(agent_name, combined,
                               msg_id=user_msgs[0]["msg_id"],
                               full_text=combined)

            _state.save_seen()

            # 方案 A + D：每轮尝试投递所有 agent 的待处理消息
            for agent_name in _state.reload_agents():
                dequeue_pending(agent_name)
            last_unread_check = check_manager_unread(last_unread_check)

        except Exception as e:
            print(f"[{time.strftime('%H:%M:%S')}] ⚠️  Router 异常: {e}")

        time.sleep(ROUTER_POLL_INTERVAL)

if __name__ == "__main__":
    main()
