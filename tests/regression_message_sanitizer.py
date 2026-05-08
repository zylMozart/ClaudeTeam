#!/usr/bin/env python3
"""Regression checks for Codex spawn command leakage into business messages."""
import json
import sys
import tempfile
from pathlib import Path

_ROOT = Path(__file__).resolve().parents[1]
for _p in (_ROOT / "scripts", _ROOT / "src", _ROOT):
    if str(_p) not in sys.path:
        sys.path.insert(0, str(_p))

import feishu_msg
from claudeteam.messaging.router import daemon as router_daemon
from claudeteam.messaging.router.daemon import _RouterRuntime
from claudeteam.storage import local_facts
from claudeteam.runtime import queue as msg_queue


SPAWN = "CODEX_AGENT=toolsmith codex --dangerously-bypass-approvals-and-sandbox"
SPAWN_MODEL = (
    "CODEX_AGENT=devops codex --dangerously-bypass-approvals-and-sandbox "
    "--model gpt-5.4"
)


def redirect_facts(tmp):
    local_facts.FACTS_DIR = local_facts.Path(tmp)
    local_facts.INBOX_FILE = local_facts.FACTS_DIR / "inbox.json"
    local_facts.STATUS_FILE = local_facts.FACTS_DIR / "status.json"
    local_facts.LOCK_FILE = local_facts.FACTS_DIR / ".facts.lock"


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
        def fake_bitable_insert_message(to, frm, content, priority, *args, **kwargs):
            captured.append(("bitable", to, frm, content, priority))
            return "rid"
        feishu_msg.bitable_insert_message = fake_bitable_insert_message
        feishu_msg.post_to_group = (
            lambda frm, to, content, priority:
            captured.append(("group", to, frm, content, priority)) or True
        )
        feishu_msg.CHAT = lambda: "test-chat-id"
        feishu_msg._lark_im_send = (
            lambda chat_id, **kw:
            captured.append(("direct_group", chat_id, kw)) or {}
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


def make_router_runtime(tmp):
    team_file = Path(tmp) / "team.json"
    team_file.write_text('{"agents":{"manager":{},"devops":{}}}\n', encoding="utf-8")
    runtime = _RouterRuntime(
        cfg={"chat_id": "", "_lark_cli": ["lark"], "_tmux_session": "sess", "_images_dir": str(tmp)},
        team_file=str(team_file),
        scripts_dir=str(Path(tmp) / "scripts"),
    )
    runtime._refresh_heartbeat = lambda: None
    runtime._advance_cursor = lambda: None
    runtime._advance_cursor_to = lambda ts: None
    runtime._load_cursor = lambda: 1776720000.0
    runtime._render_inbox = lambda text: text
    runtime._render_tmux = lambda title, subtitle, content, agent: content
    runtime._adapter = lambda agent: type("Adapter", (), {
        "process_name": lambda self: "claude",
        "ready_markers": lambda self: ["ready"],
        "submit_keys": lambda self: ["Enter"],
    })()
    return runtime


def check_router_default_route():
    injected = []
    with tempfile.TemporaryDirectory() as tmp:
        runtime = make_router_runtime(tmp)
        old_wake = router_daemon.wake_on_deliver
        old_inject = router_daemon.inject_when_idle if hasattr(router_daemon, "inject_when_idle") else None
        try:
            router_daemon.wake_on_deliver = lambda *a, **kw: True
            import claudeteam.runtime.tmux_utils as tmux_utils
            old_tmux_inject = tmux_utils.inject_when_idle
            tmux_utils.inject_when_idle = lambda session, agent, prompt, **kw: injected.append((agent, prompt)) or True
            runtime._has_pending = lambda agent: False
            runtime._enqueue = lambda agent, prompt, msg_id, is_user_msg=False: injected.append((agent, prompt))
            runtime.handle_event({
                "message_id": "regression-msg-1",
                "chat_id": "",
                "sender_id": "user-open-id",
                "text": f"{SPAWN_MODEL}\n默认路由任务",
                "message_type": "text",
            })
        finally:
            router_daemon.wake_on_deliver = old_wake
            if old_inject is not None:
                router_daemon.inject_when_idle = old_inject
            tmux_utils.inject_when_idle = old_tmux_inject

    assert injected, "router did not inject"
    agent, prompt = injected[0]
    assert_eq(agent, "manager", "router default target")
    assert_not_contains(prompt, "CODEX_AGENT=", "router default route")
    assert "默认路由任务" in prompt


def check_router_wake_failure_queues_without_injecting():
    calls = []
    with tempfile.TemporaryDirectory() as tmp:
        runtime = make_router_runtime(tmp)
        old_wake = router_daemon.wake_on_deliver
        try:
            router_daemon.wake_on_deliver = lambda *a, **kw: False
            import claudeteam.runtime.tmux_utils as tmux_utils
            old_tmux_inject = tmux_utils.inject_when_idle
            tmux_utils.inject_when_idle = lambda session, agent, prompt, **kw: calls.append(("inject", agent, prompt)) or True
            runtime._has_pending = lambda agent: False
            runtime._enqueue = lambda agent, prompt, msg_id, is_user_msg=False: calls.append(("enqueue", agent, prompt, msg_id, is_user_msg))
            runtime.handle_event({
                "message_id": "regression-wake-fail-1",
                "chat_id": "",
                "sender_id": "user-open-id",
                "text": "worker task after wake failure",
                "message_type": "text",
            })
        finally:
            router_daemon.wake_on_deliver = old_wake
            tmux_utils.inject_when_idle = old_tmux_inject

    assert calls, "wake failure did not enqueue"
    assert calls[0][0] == "enqueue", calls
    assert not any(c[0] == "inject" for c in calls), calls


def check_history_replay_route():
    injected = []
    with tempfile.TemporaryDirectory() as tmp:
        runtime = make_router_runtime(tmp)
        old_wake = router_daemon.wake_on_deliver
        try:
            router_daemon.wake_on_deliver = lambda *a, **kw: True
            import claudeteam.runtime.tmux_utils as tmux_utils
            old_tmux_inject = tmux_utils.inject_when_idle
            tmux_utils.inject_when_idle = lambda session, agent, prompt, **kw: injected.append((agent, prompt)) or True
            runtime._has_pending = lambda agent: False
            runtime._enqueue = lambda agent, prompt, msg_id, is_user_msg=False: injected.append((agent, prompt))
            from claudeteam.integrations.feishu import client as feishu_client
            old_lark_run = feishu_client._lark_run
            feishu_client._lark_run = lambda args, timeout=40: {
                "messages": [{
                    "message_id": "regression-replay-1",
                    "create_time": "1776720001000",
                    "sender": {"sender_type": "user", "id": "user-open-id"},
                    "msg_type": "text",
                    "content": json.dumps({"text": f"{SPAWN}\nreplay任务\n{SPAWN_MODEL}"}),
                }],
                "has_more": False,
            }
            replayed = runtime.catchup_from_history("chat-id")
        finally:
            router_daemon.wake_on_deliver = old_wake
            tmux_utils.inject_when_idle = old_tmux_inject
            feishu_client._lark_run = old_lark_run

    assert_eq(replayed, 1, "history replay count")
    assert injected, "history replay did not inject"
    agent, prompt = injected[0]
    assert_eq(agent, "manager", "history replay target")
    assert_not_contains(prompt, "CODEX_AGENT=", "history replay")
    assert "replay任务" in prompt


def main():
    with tempfile.TemporaryDirectory() as tmp:
        redirect_facts(tmp)
        check_sanitizer()
        check_send_direct()
        check_say_file()
        check_queue()
        check_router_default_route()
        check_router_wake_failure_queues_without_injecting()
        check_history_replay_route()
    print("✅ message sanitizer regression passed")


if __name__ == "__main__":
    main()
