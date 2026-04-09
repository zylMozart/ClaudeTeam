#!/usr/bin/env python3
"""
Router Daemon — 轮询飞书群组消息，解析 @mention，路由到对应 tmux 窗口
运行：python3 scripts/feishu_router.py
"""
import sys, os, json, time, re, subprocess, requests, threading
from concurrent.futures import ThreadPoolExecutor

sys.path.insert(0, os.path.dirname(__file__))
from config import APP_ID, APP_SECRET, BASE, AGENTS, TMUX_SESSION, ROUTER_POLL_INTERVAL, CONFIG_FILE, PROJECT_ROOT
from tmux_utils import inject_when_idle, is_agent_idle

IMAGES_DIR = os.path.join(PROJECT_ROOT, "workspace", "shared", "images")

from token_cache import get_token_cached, invalidate as _invalidate_token

def get_token():
    return get_token_cached(APP_ID, APP_SECRET, BASE)

def h(token):
    return {"Authorization": f"Bearer {token}", "Content-Type": "application/json"}

def load_cfg():
    with open(CONFIG_FILE) as f:
        return json.load(f)

# ── 已处理消息 ID（防重复）────────────────────────────────────

SEEN_IDS_FILE = os.path.join(os.path.dirname(__file__), ".router_seen_ids.json")

def load_seen():
    if os.path.exists(SEEN_IDS_FILE):
        with open(SEEN_IDS_FILE) as f:
            return set(json.load(f))
    return set()

def save_seen(ids):
    # 只保留最近 500 条，防止文件无限增长
    lst = list(ids)[-500:]
    with open(SEEN_IDS_FILE, "w") as f:
        json.dump(lst, f)

# ── Agent 列表热加载（基于 team.json mtime）─────────────────────

_team_file = os.path.join(PROJECT_ROOT, "team.json")
_team_mtime = 0
_agent_names = []

def reload_agents():
    """检查 team.json 变更，热加载 agent 列表"""
    global _team_mtime, _agent_names
    try:
        mt = os.path.getmtime(_team_file)
        if mt != _team_mtime:
            with open(_team_file) as f:
                data = json.load(f)
            _agent_names = list(data.get("agents", {}).keys())
            _team_mtime = mt
            print(f"🔄 Agent 列表已刷新: {', '.join(_agent_names)}")
    except Exception as e:
        print(f"⚠️ reload_agents 失败: {e}")
    return _agent_names

# ── 解析消息中的 @agent 指令 ─────────────────────────────────

def parse_targets(text):
    """从消息文本中提取被 @mention 的 agent 名称列表"""
    found = []
    for name in reload_agents():
        if f"@{name}" in text:
            found.append(name)
    return found

def parse_sender(text):
    """从消息格式 【agent · role】 中提取发件人"""
    m = re.search(r"【(\w[\w-]*)[\s·]", text)
    if m:
        name = m.group(1)
        if name in reload_agents():
            return name
    return None

# ── 消息待投递队列（方案 A）─────────────────────────────────────

PENDING_DIR = os.path.join(PROJECT_ROOT, "workspace", "shared", ".pending_msgs")
_queue_lock = threading.Lock()  # 保护队列文件读写（回调线程 + 主线程）

def queue_message(agent_name, msg_text, msg_id, is_user_msg=False):
    """将消息加入待投递队列（线程安全）"""
    with _queue_lock:
        os.makedirs(PENDING_DIR, exist_ok=True)
        queue_file = os.path.join(PENDING_DIR, f"{agent_name}.json")

        queue = []
        if os.path.exists(queue_file):
            with open(queue_file) as f:
                queue = json.load(f)

        # 去重：同一 msg_id 不重复入队
        if any(m["msg_id"] == msg_id for m in queue):
            return

        queue.append({
            "msg_id": msg_id,
            "text": msg_text,
            "is_user_msg": is_user_msg,
            "queued_at": time.time(),
            "attempts": 0,
            "last_attempt": 0,
        })

        _save_queue_unlocked(agent_name, queue)

def _save_queue_unlocked(agent_name, queue):
    """保存队列，清理超过 10 分钟的过期消息（调用方需持有 _queue_lock）"""
    now = time.time()
    queue = [m for m in queue if now - m["queued_at"] < 600]

    os.makedirs(PENDING_DIR, exist_ok=True)
    queue_file = os.path.join(PENDING_DIR, f"{agent_name}.json")
    with open(queue_file, "w") as f:
        json.dump(queue, f, ensure_ascii=False)

def try_deliver_pending(agent_name):
    """尝试投递队列中的待处理消息（线程安全）"""
    with _queue_lock:
        queue_file = os.path.join(PENDING_DIR, f"{agent_name}.json")
        if not os.path.exists(queue_file):
            return

        with open(queue_file) as f:
            queue = json.load(f)

        if not queue:
            return

        # 检查 agent 是否空闲
        if not is_agent_idle(TMUX_SESSION, agent_name):
            # 不空闲：检查是否需要紧急升级（用户消息等待超过 30 秒）
            oldest_user_msg = next(
                (m for m in queue if m["is_user_msg"]), None
            )
            if oldest_user_msg:
                wait_time = time.time() - oldest_user_msg["queued_at"]
                if wait_time > 30 and oldest_user_msg["attempts"] < 3:
                    urgent_prompt = (
                        f"⚠️【紧急】你有 {len(queue)} 条未处理消息"
                        f"（用户消息等待 {int(wait_time)} 秒）。\n"
                        f"请尽快处理当前任务后执行: "
                        f"python3 scripts/feishu_msg.py inbox {agent_name}"
                    )
                    inject_when_idle(TMUX_SESSION, agent_name, urgent_prompt,
                                    wait_secs=2, force_after_wait=True)
                    oldest_user_msg["attempts"] += 1
                    oldest_user_msg["last_attempt"] = time.time()
                    _save_queue_unlocked(agent_name, queue)
            return

        # 空闲：投递队列中最早的消息
        msg = queue[0]
        ok = inject_when_idle(TMUX_SESSION, agent_name, msg["text"],
                              wait_secs=2, force_after_wait=False)
        if ok:
            queue.pop(0)
            print(f"  ✅ 待投递消息已送达 {agent_name} (msg_id: {msg['msg_id'][:8]})")
        else:
            msg["attempts"] += 1
            msg["last_attempt"] = time.time()

        _save_queue_unlocked(agent_name, queue)

# ── Manager 未读提醒（方案 D）───────────────────────────────────

_last_unread_check = 0
UNREAD_CHECK_INTERVAL = 30  # 秒

def check_manager_unread():
    """检查 manager 是否有积压的未读用户消息"""
    global _last_unread_check
    now = time.time()
    if now - _last_unread_check < UNREAD_CHECK_INTERVAL:
        return
    _last_unread_check = now

    queue_file = os.path.join(PENDING_DIR, "manager.json")
    if not os.path.exists(queue_file):
        return

    with open(queue_file) as f:
        queue = json.load(f)

    user_msgs = [m for m in queue if m["is_user_msg"]]
    if not user_msgs:
        return

    oldest_wait = now - min(m["queued_at"] for m in user_msgs)
    if oldest_wait > 60:
        if is_agent_idle(TMUX_SESSION, "manager"):
            try_deliver_pending("manager")
        else:
            print(f"  ⚠️ Manager 有 {len(user_msgs)} 条用户消息积压 {int(oldest_wait)}s")

# ── 触发 tmux 窗口 ────────────────────────────────────────────

def wake_agent(agent_name, message_preview, sender_agent=None,
               full_text=None, msg_id=""):
    """向 agent 投递消息：先尝试直接投递，忙碌则入队"""
    # 构建 prompt
    if sender_agent:
        prompt = (
            f"【Router】你有来自 {sender_agent} 的新消息。\n"
            f"执行: python3 scripts/feishu_msg.py inbox {agent_name}\n"
            f"消息预览: {message_preview[:500]}"
        )
    else:
        content = full_text or message_preview
        if len(content) > 400:
            msg_file = os.path.join(PROJECT_ROOT, "workspace", "shared",
                                    f".router_msg_{agent_name}.txt")
            os.makedirs(os.path.dirname(msg_file), exist_ok=True)
            with open(msg_file, "w", encoding="utf-8") as f:
                f.write(content)
            prompt = (
                f"【群聊消息】用户在群里发了消息（较长，已保存到文件）。\n"
                f"请先读取文件: {msg_file}\n"
                f"预览: {content[:200]}\n\n"
                f"处理完成后用以下命令回复群里:\n"
                f"python3 scripts/feishu_msg.py say {agent_name} \"<你的回复>\""
            )
        else:
            prompt = (
                f"【群聊消息】用户在群里对你说:\n{content}\n\n"
                f"请直接处理，然后用以下命令回复群里:\n"
                f"python3 scripts/feishu_msg.py say {agent_name} \"<你的回复>\""
            )

    is_user_msg = (sender_agent is None)

    # 先尝试直接投递（agent 空闲时）
    if is_agent_idle(TMUX_SESSION, agent_name):
        ok = inject_when_idle(TMUX_SESSION, agent_name, prompt,
                              wait_secs=2, force_after_wait=False)
        if ok:
            print(f"  → 已触发 {agent_name} 窗口（直接投递）")
            return

    # agent 忙碌 → 入队等待
    queue_message(agent_name, prompt, msg_id, is_user_msg=is_user_msg)
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
            notify = (
                f"【Router 补充】之前的图片已下载完成。\n"
                f"本地路径: {local_path}\n"
                f"你可以使用 Read 工具查看图片。"
            )
            queue_message(agent_name, notify, f"{msg_id}_img", is_user_msg=True)
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

# ── 主循环 ────────────────────────────────────────────────────

def _startup_token(max_retries=5, delay=3):
    """启动时获取 token，带重试（避免 setup 刚完成 token 未就绪）"""
    for attempt in range(1, max_retries + 1):
        try:
            token = get_token()
            # 验证 token 可用：发一个轻量请求
            r = requests.get(f"{BASE}/im/v1/chats?page_size=1",
                             headers=h(token))
            if r.json().get("code") == 0:
                return token
            raise RuntimeError(r.json().get("msg", "unknown"))
        except Exception as e:
            print(f"  ⚠️  Token 获取失败 (第{attempt}次): {e}")
            if attempt < max_retries:
                time.sleep(delay)
    print("❌ 启动失败：无法获取有效 Token，请检查 .env 凭证和网络")
    sys.exit(1)

def main():
    print("🚀 Router Daemon 启动")
    cfg = load_cfg()
    chat_id = cfg.get("chat_id", "")
    if not chat_id:
        print("❌ chat_id 未配置，请先运行 setup.py")
        sys.exit(1)

    # 启动时验证 token，带重试
    _startup_token()
    print("✅ Token 验证通过")

    seen = load_seen()
    # 从当前时间开始，只处理新消息
    since_ts = int(time.time() * 1000)

    print(f"💬 监听群组: {chat_id}")
    print(f"🔄 轮询间隔: {ROUTER_POLL_INTERVAL}s")
    print(f"👥 Agent 列表: {', '.join(reload_agents())}")
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
                if msg_id in seen:
                    continue
                seen.add(msg_id)

                # 更新时间窗口（用当前本地时间，避免 since_ts 超过服务器时间）
                create_ts = int(msg.get("create_time", "0"))
                if create_ts * 1000 > since_ts:
                    since_ts = int(time.time() * 1000)

                # 解析消息内容（按 msg_type 分支处理）
                msg_type    = msg.get("msg_type", "text")
                content_raw = msg.get("body", {}).get("content", "{}")
                create_time = msg.get("create_time", str(int(time.time())))

                if msg_type == "image":
                    # 方案 C：图片异步下载，先投递占位通知
                    content_obj = json.loads(content_raw) if content_raw else {}
                    image_key   = content_obj.get("image_key", "")
                    text = f"[图片消息] image_key: {image_key}（下载中...）"
                    # 异步下载（目标 agent 在路由阶段确定后再绑定回调）
                    _img_futures.append({
                        "future": _download_pool.submit(
                            download_image, token, msg_id, image_key, create_time),
                        "msg_id": msg_id,
                    })

                elif msg_type == "text":
                    try:
                        content_obj = json.loads(content_raw)
                        text = content_obj.get("text", "")
                    except Exception:
                        text = content_raw

                elif msg_type == "post":
                    try:
                        content_obj = json.loads(content_raw) if content_raw else {}
                    except Exception:
                        content_obj = {}
                    paragraphs = content_obj.get("zh_cn", [])
                    text_parts = []
                    for para in paragraphs:
                        for elem in para:
                            if elem.get("tag") == "text":
                                text_parts.append(elem.get("text", ""))
                            elif elem.get("tag") == "img":
                                image_key = elem.get("image_key", "")
                                if image_key:
                                    # 方案 C：图片异步下载
                                    _img_futures.append({
                                        "future": _download_pool.submit(
                                            download_image, token, msg_id,
                                            image_key, create_time),
                                        "msg_id": msg_id,
                                    })
                    text = " ".join(text_parts).strip()
                    if not text:
                        text = "[富文本消息] 图片正在下载中..."

                elif msg_type == "interactive":
                    # 消息卡片：飞书 API 返回的 body.content 是简化格式
                    # 格式: {"title":"emoji agent · role","elements":[[{"tag":"text","text":"内容"}]]}
                    try:
                        card = json.loads(content_raw) if content_raw else {}
                        # title 是扁平字符串（非嵌套的 header.title.content）
                        header_text = card.get("title", "")
                        if not header_text:
                            header_text = card.get("header", {}).get("title", {}).get("content", "")
                        # elements 是双层数组 [[{tag,text},...],...]
                        body_parts = []
                        for row in card.get("elements", []):
                            if isinstance(row, list):
                                for elem in row:
                                    if isinstance(elem, dict):
                                        body_parts.append(elem.get("text", "") or elem.get("content", ""))
                            elif isinstance(row, dict):
                                body_parts.append(row.get("content", "") or row.get("text", ""))
                        body_text = "\n".join(p for p in body_parts if p)
                        text = f"{header_text}\n{body_text}" if header_text else body_text
                    except Exception:
                        text = content_raw

                else:
                    print(f"  ⏭️  跳过不支持的消息类型: {msg_type}")
                    continue

                sender_id = msg.get("sender", {}).get("id", "")
                print(f"[{time.strftime('%H:%M:%S')}] 新消息: {text[:10000]}")

                sender_agent = parse_sender(text)
                targets = parse_targets(text)

                # 图片/富文本消息生成更具体的 full_text 提示
                if msg_type in ("image", "post") and not sender_agent:
                    has_images = "图片路径:" in text or "image_key:" in text
                    if has_images:
                        full_text_override = (
                            f"【群聊消息】用户在群里发送了图片/富文本消息:\n{text}\n\n"
                            f"你可以使用 Read 工具读取图片。处理完成后用以下命令回复群里:\n"
                            f"python3 scripts/feishu_msg.py say manager \"<你的回复>\""
                        )
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
                    combined = f"【群聊消息】用户连续发了 {len(user_msgs)} 条消息:\n\n"
                    for i, um in enumerate(user_msgs, 1):
                        content = um["full_text"] or um["text"]
                        combined += f"--- 第 {i} 条 ---\n{content}\n\n"
                    combined += (
                        f"请逐条处理，然后用以下命令回复群里:\n"
                        f"python3 scripts/feishu_msg.py say {agent_name} "
                        f"\"<你的回复>\""
                    )
                    wake_agent(agent_name, combined,
                               msg_id=user_msgs[0]["msg_id"],
                               full_text=combined)

            save_seen(seen)

            # 方案 A + D：每轮尝试投递所有 agent 的待处理消息
            for agent_name in reload_agents():
                try_deliver_pending(agent_name)
            check_manager_unread()

        except Exception as e:
            print(f"[{time.strftime('%H:%M:%S')}] ⚠️  Router 异常: {e}")

        time.sleep(ROUTER_POLL_INTERVAL)

if __name__ == "__main__":
    main()
