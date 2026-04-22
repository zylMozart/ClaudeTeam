#!/usr/bin/env python3
"""
消息队列管理模块 — ClaudeTeam

管理 agent 消息的待投递队列：入队、出队、过期清理、FIFO 保证。
队列以 JSON 文件持久化在 PENDING_DIR 下，每个 agent 一个文件。
"""
import os, json, time, threading

import sys
sys.path.insert(0, os.path.dirname(__file__))
from config import PROJECT_ROOT, TMUX_SESSION
from feishu_msg import bitable_insert_message, sanitize_agent_message
from message_renderer import render_tmux_prompt
from tmux_utils import inject_when_idle, is_agent_idle
from cli_adapters import adapter_for_agent

PENDING_DIR = os.path.join(PROJECT_ROOT, "workspace", "shared", ".pending_msgs")
_queue_lock = threading.Lock()

EXPIRE_SECS = 600  # 10 分钟过期
USER_MSG_EXPIRE_ALERT_INTERVAL = 300  # 用户消息过期后仍保留,最多 5 分钟告警一次
URGENT_WAIT_SECS = 30  # 用户消息等待超过此时间则紧急升级
UNREAD_CHECK_INTERVAL = 30  # manager 未读检查间隔
EXPIRY_ALERT_RECIPIENTS = ("manager", "devops")


def enqueue_message(agent_name, msg_text, msg_id, is_user_msg=False):
    """将消息加入待投递队列（线程安全）。"""
    msg_text = sanitize_agent_message(msg_text)
    with _queue_lock:
        os.makedirs(PENDING_DIR, exist_ok=True)
        queue_file = os.path.join(PENDING_DIR, f"{agent_name}.json")

        queue = []
        if os.path.exists(queue_file):
            with open(queue_file) as f:
                queue = json.load(f)

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


def has_pending_messages(agent_name):
    """检查 agent 是否有待投递消息（线程安全）。"""
    with _queue_lock:
        queue_file = os.path.join(PENDING_DIR, f"{agent_name}.json")
        if not os.path.exists(queue_file):
            return False
        with open(queue_file) as f:
            pending = json.load(f)
        return len(pending) > 0


def dequeue_pending(agent_name):
    """尝试投递队列中最早的待处理消息（线程安全）。"""
    with _queue_lock:
        queue_file = os.path.join(PENDING_DIR, f"{agent_name}.json")
        if not os.path.exists(queue_file):
            return

        with open(queue_file) as f:
            queue = json.load(f)

        if not queue:
            return

        if not is_agent_idle(TMUX_SESSION, agent_name):
            _handle_busy_agent(agent_name, queue)
            return

        msg = queue[0]
        msg["text"] = sanitize_agent_message(msg["text"])
        ok = inject_when_idle(TMUX_SESSION, agent_name, msg["text"],
                              wait_secs=2, force_after_wait=False,
                              submit_keys=adapter_for_agent(agent_name).submit_keys())
        if ok:
            queue.pop(0)
            print(f"  ✅ 待投递消息已送达 {agent_name} (msg_id: {msg['msg_id'][:8]})")
        else:
            msg["attempts"] += 1
            msg["last_attempt"] = time.time()
            detail = getattr(ok, "error", "") or "not submitted"
            print(f"  ⚠️ 待投递消息未提交 {agent_name}: {detail}")

        _save_queue_unlocked(agent_name, queue)


def check_manager_unread(last_check_time):
    """检查 manager 是否有积压的未读用户消息。返回更新后的 check 时间。

    P1-2 修复: 原版读 manager.json 未加锁,并发下可能读到 enqueue/dequeue
    写一半的 JSON,抛 JSONDecodeError,router 后台线程 try/except 吞掉但日志
    留下神秘 warning。这里把读取段包进 _queue_lock 里,和其他 queue 操作的
    并发语义对齐。
    dequeue_pending() 本身持有 _queue_lock,必须在 with 块外调用,否则会
    因为 _queue_lock 不可重入(默认 threading.Lock)而死锁。
    """
    now = time.time()
    if now - last_check_time < UNREAD_CHECK_INTERVAL:
        return last_check_time

    queue_file = os.path.join(PENDING_DIR, "manager.json")

    with _queue_lock:
        if not os.path.exists(queue_file):
            return now
        with open(queue_file) as f:
            queue = json.load(f)

    user_msgs = [m for m in queue if m["is_user_msg"]]
    if not user_msgs:
        return now

    oldest_wait = now - min(m["queued_at"] for m in user_msgs)
    if oldest_wait > 60:
        if is_agent_idle(TMUX_SESSION, "manager"):
            dequeue_pending("manager")   # 自己持锁,必须在 with 块外
        else:
            print(f"  ⚠️ Manager 有 {len(user_msgs)} 条用户消息积压 {int(oldest_wait)}s")

    return now


def _handle_busy_agent(agent_name, queue):
    """agent 忙碌时，检查是否需要紧急升级用户消息。
    调用方需持有 _queue_lock。"""
    oldest_user_msg = next(
        (m for m in queue if m["is_user_msg"]), None
    )
    if oldest_user_msg:
        wait_time = time.time() - oldest_user_msg["queued_at"]
        if wait_time > URGENT_WAIT_SECS and oldest_user_msg["attempts"] < 3:
            urgent_prompt = (
                f"⚠️【紧急】你有 {len(queue)} 条未处理消息"
                f"（用户消息等待 {int(wait_time)} 秒）。\n"
                f"请尽快处理当前任务后执行: "
                f"python3 scripts/feishu_msg.py inbox {agent_name}"
            )
            urgent_prompt = render_tmux_prompt(
                "紧急提醒", "待处理消息积压", urgent_prompt)
            ok = inject_when_idle(TMUX_SESSION, agent_name, urgent_prompt,
                                  wait_secs=2, force_after_wait=False,
                                  submit_keys=adapter_for_agent(agent_name).submit_keys())
            if not ok:
                detail = getattr(ok, "error", "") or "not submitted"
                print(f"  ⚠️ [{agent_name}] 紧急提醒未注入: {detail}")
            oldest_user_msg["attempts"] += 1
            oldest_user_msg["last_attempt"] = time.time()
            _save_queue_unlocked(agent_name, queue)


def _save_queue_unlocked(agent_name, queue):
    """保存队列，清理过期消息。调用方需持有 _queue_lock。"""
    now = time.time()
    queue_file = os.path.join(PENDING_DIR, f"{agent_name}.json")

    expired = [m for m in queue if now - m["queued_at"] >= EXPIRE_SECS]
    for m in expired:
        wait_secs = int(now - m["queued_at"])
        msg_id_short = m.get("msg_id", "?")[:8]
        if m.get("is_user_msg"):
            last_alert = float(m.get("expiry_alerted_at") or 0)
            if now - last_alert >= USER_MSG_EXPIRE_ALERT_INTERVAL:
                print(
                    f"  ⚠️ [{agent_name}] 用户消息超过 {EXPIRE_SECS}s 仍未送达,"
                    f" 已保留不丢弃: msg_id={msg_id_short}, 等待 {wait_secs}s;"
                    f" 请检查 pending 队列或执行 python3 scripts/feishu_msg.py inbox {agent_name}"
                )
                _send_user_msg_expiry_alert(agent_name, m, wait_secs, queue_file)
                m["expiry_alerted_at"] = now
        else:
            print(f"  🗑️ [{agent_name}] 队列消息过期丢弃: msg_id={msg_id_short}, 等待 {wait_secs}s")

    queue = [
        m for m in queue
        if m.get("is_user_msg") or now - m["queued_at"] < EXPIRE_SECS
    ]

    os.makedirs(PENDING_DIR, exist_ok=True)
    with open(queue_file, "w") as f:
        json.dump(queue, f, ensure_ascii=False)


def _send_user_msg_expiry_alert(agent_name, msg, wait_secs, queue_file):
    """写入 manager/devops 可见 inbox 告警,不走 tmux/队列,避免递归。"""
    msg_id_short = str(msg.get("msg_id") or "?")[:8]
    content = (
        "【Router pending 告警】用户消息超过队列保留阈值仍未送达,已保留不丢弃。\n"
        f"- agent: {agent_name}\n"
        f"- msg_id: {msg_id_short}\n"
        f"- 等待秒数: {wait_secs}\n"
        f"- queue file: {queue_file}\n"
        f"- 建议检查命令: python3 scripts/feishu_msg.py inbox {agent_name}\n"
        f"- 队列检查: cat {queue_file}"
    )
    for recipient in EXPIRY_ALERT_RECIPIENTS:
        try:
            rid = bitable_insert_message(recipient, "router", content, "中")
            if rid:
                print(f"  ⚠️ [{agent_name}] pending 告警已写入 {recipient} inbox [rid: {rid}]")
            else:
                print(f"  ⚠️ [{agent_name}] pending 告警写入 {recipient} inbox 失败")
        except Exception as e:
            print(f"  ⚠️ [{agent_name}] pending 告警写入 {recipient} inbox 异常: {e}")
