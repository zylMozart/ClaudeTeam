#!/usr/bin/env python3
"""Regression checks for Codex spawn command leakage into business messages."""
import json
import tempfile
import sys

import feishu_msg
import feishu_router
import msg_queue


SPAWN = "CODEX_AGENT=toolsmith codex --dangerously-bypass-approvals-and-sandbox"
SPAWN_MODEL = (
    "CODEX_AGENT=devops codex --dangerously-bypass-approvals-and-sandbox "
    "--model gpt-5.4"
)


def assert_eq(actual, expected, label):
    if actual != expected:
        raise AssertionError(f"{label}: expected {expected!r}, got {actual!r}")


def assert_not_contains(text, needle, label):
    if needle in text:
        raise AssertionError(f"{label}: leaked {needle!r} in {text!r}")


def check_sanitizer():
    cases = {
        "whole line": (f"{SPAWN}\n真实任务", "真实任务"),
        "glued prefix": (f"{SPAWN}真实任务", "真实任务"),
        "prefix with model": (f"{SPAWN_MODEL} 真实任务", "真实任务"),
        "suffix example preserved": (f"真实任务 {SPAWN}", f"真实任务 {SPAWN}"),
        "middle multiline": (f"第一行\n{SPAWN_MODEL}\n第二行", "第一行\n第二行"),
        "placeholder example preserved": (
            "CODEX_AGENT=<agent> codex --dangerously-bypass-approvals-and-sandbox "
            "--model gpt-5.4 -c 'model_reasoning_effort=\"high\"'",
            "CODEX_AGENT=&lt;agent&gt; codex --dangerously-bypass-approvals-and-sandbox "
            "--model gpt-5.4 -c 'model_reasoning_effort=\"high\"'",
        ),
    }
    for label, (raw, expected) in cases.items():
        assert_eq(feishu_msg.sanitize_agent_message(raw), expected, label)


def check_send_direct():
    captured = []
    old = (
        feishu_msg.bitable_insert_message,
        feishu_msg.post_to_group,
        feishu_msg.CHAT,
        feishu_msg._lark_im_send,
        feishu_msg.ws_log,
        feishu_msg._notify_agent_tmux,
    )
    try:
        feishu_msg.bitable_insert_message = (
            lambda to, frm, content, priority:
            captured.append(("bitable", to, frm, content, priority)) or "rid"
        )
        feishu_msg.post_to_group = (
            lambda frm, to, content, priority:
            captured.append(("group", to, frm, content, priority)) or True
        )
        feishu_msg.CHAT = lambda: "chat-id"
        feishu_msg._lark_im_send = (
            lambda chat_id, **kw: captured.append(("direct_group", chat_id, kw)) or {}
        )
        feishu_msg.ws_log = (
            lambda agent, typ, content, ref="":
            captured.append(("log", agent, typ, content, ref))
        )
        feishu_msg._notify_agent_tmux = (
            lambda to, frm, content:
            captured.append(("notify", to, frm, content))
        )
        feishu_msg.cmd_send("devops", "manager", f"{SPAWN}真实任务", "高")
        feishu_msg.cmd_direct("toolsmith", "manager", f"直连任务\n{SPAWN_MODEL}")
    finally:
        (
            feishu_msg.bitable_insert_message,
            feishu_msg.post_to_group,
            feishu_msg.CHAT,
            feishu_msg._lark_im_send,
            feishu_msg.ws_log,
            feishu_msg._notify_agent_tmux,
        ) = old

    joined = json.dumps(captured, ensure_ascii=False)
    assert_not_contains(joined, "CODEX_AGENT=", "send/direct")
    assert "真实任务" in joined and "直连任务" in joined


def check_say_file():
    captured = []
    old_argv = sys.argv
    old = (
        feishu_msg._lark_im_send,
        feishu_msg.CHAT,
        feishu_msg.ws_log,
    )
    with tempfile.NamedTemporaryFile("w+", encoding="utf-8") as tmp:
        tmp.write(
            "Codex command:\n"
            "CODEX_AGENT=<agent> codex --dangerously-bypass-approvals-and-sandbox "
            "--model gpt-5.4 -c 'model_reasoning_effort=\"high\"'\n"
            "Use <model> safely."
        )
        tmp.flush()
        try:
            sys.argv = ["feishu_msg.py", "say", "manager", "--file", tmp.name]
            feishu_msg.CHAT = lambda: "chat-id"
            feishu_msg._lark_im_send = (
                lambda chat_id, **kw: captured.append((chat_id, kw)) or {}
            )
            feishu_msg.ws_log = lambda *a, **kw: None
            feishu_msg.main()
        finally:
            sys.argv = old_argv
            (
                feishu_msg._lark_im_send,
                feishu_msg.CHAT,
                feishu_msg.ws_log,
            ) = old

    assert captured, "say --file did not send"
    card = captured[0][1]["card"]
    content = card["elements"][0]["content"]
    assert "CODEX_AGENT=&lt;agent&gt;" in content, content
    assert "model_reasoning_effort" in content, content
    assert "&lt;model&gt;" in content, content


def check_queue():
    injected = []
    old_dir = msg_queue.PENDING_DIR
    old_idle = msg_queue.is_agent_idle
    old_inject = msg_queue.inject_when_idle
    with tempfile.TemporaryDirectory() as tmp:
        try:
            msg_queue.PENDING_DIR = tmp
            msg_queue.is_agent_idle = lambda session, agent: True
            msg_queue.inject_when_idle = (
                lambda session, agent, text, **kw:
                injected.append(text) or True
            )
            msg_queue.enqueue_message("toolsmith", f"{SPAWN}\n排队任务", "qid")
            msg_queue.dequeue_pending("toolsmith")
        finally:
            msg_queue.PENDING_DIR = old_dir
            msg_queue.is_agent_idle = old_idle
            msg_queue.inject_when_idle = old_inject

    assert_eq(injected, ["排队任务"], "queue inject")


def check_router_default_route():
    injected = []
    old = (
        feishu_router.wake_on_deliver,
        feishu_router.has_pending_messages,
        feishu_router.inject_when_idle,
        feishu_router.enqueue_message,
        feishu_router._advance_cursor,
    )
    try:
        feishu_router._state.seen_ids.clear()
        feishu_router._state.chat_id = ""
        feishu_router.wake_on_deliver = lambda agent: True
        feishu_router.has_pending_messages = lambda agent: False
        feishu_router.inject_when_idle = (
            lambda session, agent, prompt, **kw:
            injected.append((agent, prompt)) or True
        )
        feishu_router.enqueue_message = (
            lambda agent, prompt, msg_id, is_user_msg=False:
            injected.append((agent, prompt))
        )
        feishu_router._advance_cursor = lambda: None
        feishu_router.handle_event({
            "message_id": "regression-msg-1",
            "chat_id": "",
            "sender_id": "user-open-id",
            "text": f"{SPAWN_MODEL}\n默认路由任务",
            "message_type": "text",
        })
    finally:
        (
            feishu_router.wake_on_deliver,
            feishu_router.has_pending_messages,
            feishu_router.inject_when_idle,
            feishu_router.enqueue_message,
            feishu_router._advance_cursor,
        ) = old

    assert injected, "router did not inject"
    agent, prompt = injected[0]
    assert_eq(agent, "manager", "router default target")
    assert_not_contains(prompt, "CODEX_AGENT=", "router default route")
    assert "默认路由任务" in prompt


def check_history_replay_route():
    injected = []
    old = (
        feishu_router._load_cursor,
        feishu_router._advance_cursor,
        feishu_router._advance_cursor_to,
        feishu_router._lark_run,
        feishu_router.wake_on_deliver,
        feishu_router.has_pending_messages,
        feishu_router.inject_when_idle,
        feishu_router.enqueue_message,
    )
    try:
        feishu_router._state.seen_ids.clear()
        feishu_router._state.chat_id = ""
        feishu_router._load_cursor = lambda: 1776720000.0
        feishu_router._advance_cursor = lambda: None
        feishu_router._advance_cursor_to = lambda ts: None
        feishu_router._lark_run = lambda args, timeout=40: {
            "messages": [{
                "message_id": "regression-replay-1",
                "create_time": "1776720001000",
                "sender": {"sender_type": "user", "id": "user-open-id"},
                "msg_type": "text",
                "content": json.dumps({
                    "text": f"{SPAWN}\nreplay任务\n{SPAWN_MODEL}"
                }),
            }],
            "has_more": False,
        }
        feishu_router.wake_on_deliver = lambda agent: True
        feishu_router.has_pending_messages = lambda agent: False
        feishu_router.inject_when_idle = (
            lambda session, agent, prompt, **kw:
            injected.append((agent, prompt)) or True
        )
        feishu_router.enqueue_message = (
            lambda agent, prompt, msg_id, is_user_msg=False:
            injected.append((agent, prompt))
        )
        replayed = feishu_router._catchup_from_history("chat-id")
    finally:
        (
            feishu_router._load_cursor,
            feishu_router._advance_cursor,
            feishu_router._advance_cursor_to,
            feishu_router._lark_run,
            feishu_router.wake_on_deliver,
            feishu_router.has_pending_messages,
            feishu_router.inject_when_idle,
            feishu_router.enqueue_message,
        ) = old

    assert_eq(replayed, 1, "history replay count")
    assert injected, "history replay did not inject"
    agent, prompt = injected[0]
    assert_eq(agent, "manager", "history replay target")
    assert_not_contains(prompt, "CODEX_AGENT=", "history replay")
    assert "replay任务" in prompt


def main():
    check_sanitizer()
    check_send_direct()
    check_say_file()
    check_queue()
    check_router_default_route()
    check_history_replay_route()
    print("✅ message sanitizer regression passed")


if __name__ == "__main__":
    main()
