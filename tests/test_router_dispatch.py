#!/usr/bin/env python3
"""Unit tests for claudeteam.messaging.router.dispatch.classify_event.

All tests are pure (no subprocess, no file I/O). The dispatch logic is
exercised via injected callables so no feishu_msg / tmux / lark-cli are
imported at test time.
"""
from __future__ import annotations

import sys
from pathlib import Path

_ROOT = Path(__file__).resolve().parents[1]
for _p in (_ROOT / "src", _ROOT):
    if str(_p) not in sys.path:
        sys.path.insert(0, str(_p))

from claudeteam.messaging.router.dispatch import classify_event, EventAction


# ── helpers ───────────────────────────────────────────────────────────────────

def _classify(event, *, chat_id="chat-1", agents=("manager", "devops"),
               bot_id="", sanitize=None, slash_cmds=("/help", "/team"),
               prefix_target=None):
    """Thin wrapper with sensible defaults."""
    sanitize = sanitize or (lambda t: t)
    kwargs = dict(
        is_seen=lambda _: False,
        is_bot_message=lambda sid: bool(bot_id and sid == bot_id),
        chat_id=chat_id,
        sanitize=sanitize,
        parse_targets=lambda t: [a for a in agents if f"@{a}" in t],
        parse_sender=lambda t: next(
            (a for a in agents if f"【{a}" in t), None),
        is_slash=lambda t: any(t.strip().startswith(c) for c in slash_cmds),
    )
    if prefix_target is not None:
        kwargs["parse_prefix_target"] = prefix_target
    return classify_event(event, **kwargs)


# ── DROP cases ────────────────────────────────────────────────────────────────

def test_drop_no_msg_id():
    r = _classify({"chat_id": "chat-1", "text": "hello"})
    assert r.action == EventAction.DROP
    assert r.reason == "no_msg_id"


def test_drop_dedup():
    seen = {"msg-42"}
    r = classify_event(
        {"message_id": "msg-42", "chat_id": "chat-1", "text": "hello"},
        is_seen=lambda mid: mid in seen,
        is_bot_message=lambda _: False,
        chat_id="chat-1",
        sanitize=lambda t: t,
        parse_targets=lambda _: [],
        parse_sender=lambda _: None,
        is_slash=lambda _: False,
    )
    assert r.action == EventAction.DROP
    assert r.reason == "dedup"


def test_drop_cross_team():
    r = _classify({"message_id": "m1", "chat_id": "other-chat", "text": "hello"},
                  chat_id="chat-1")
    assert r.action == EventAction.DROP
    assert r.reason == "cross_team"


def test_drop_bot_self_message():
    r = _classify({"message_id": "m1", "chat_id": "chat-1",
                   "sender_id": "bot-123", "text": "I am the bot"},
                  bot_id="bot-123")
    assert r.action == EventAction.DROP
    assert r.reason == "bot_self"


def test_drop_empty_text_after_sanitize():
    r = _classify({"message_id": "m1", "chat_id": "chat-1", "text": "dirty"},
                  sanitize=lambda _: "")
    assert r.action == EventAction.DROP
    assert r.reason == "empty_text"


def test_drop_agent_broadcast_no_target():
    """Agent sends a message with no @-mentions → nothing to route."""
    r = _classify({"message_id": "m1", "chat_id": "chat-1",
                   "text": "【devops·操作中】部署完成"})
    assert r.action == EventAction.DROP
    assert r.reason == "agent_no_target"


# ── SLASH cases ───────────────────────────────────────────────────────────────

def test_slash_command_detected():
    r = _classify({"message_id": "m1", "chat_id": "chat-1", "text": "/help"})
    assert r.action == EventAction.SLASH
    assert r.text == "/help"


def test_slash_team_command():
    r = _classify({"message_id": "m1", "chat_id": "chat-1", "text": "/team"})
    assert r.action == EventAction.SLASH


# ── ROUTE cases ───────────────────────────────────────────────────────────────

def test_route_default_to_manager_for_user_message():
    r = _classify({"message_id": "m1", "chat_id": "chat-1",
                   "text": "请处理一下这个问题"})
    assert r.action == EventAction.ROUTE
    assert r.targets == ["manager"]
    assert r.sender is None


def test_route_at_mention_single_target():
    r = _classify({"message_id": "m1", "chat_id": "chat-1",
                   "text": "@devops 请你帮忙部署"})
    assert r.action == EventAction.ROUTE
    assert "devops" in r.targets


def test_route_at_mention_multiple_targets():
    r = _classify({"message_id": "m1", "chat_id": "chat-1",
                   "text": "@manager @devops 紧急情况"})
    assert r.action == EventAction.ROUTE
    assert set(r.targets) == {"manager", "devops"}


def test_route_excludes_sender_from_targets():
    """Agent message @-mentioning itself should not self-deliver."""
    r = _classify({"message_id": "m1", "chat_id": "chat-1",
                   "text": "【manager·任务中】@devops 请接手"},
                  agents=("manager", "devops"))
    assert r.action == EventAction.ROUTE
    assert "manager" not in r.targets
    assert "devops" in r.targets
    assert r.sender == "manager"


def test_route_text_is_sanitized():
    def _sanitize(t):
        return t.replace("CODEX_AGENT=foo codex", "")

    r = _classify({"message_id": "m1", "chat_id": "chat-1",
                   "text": "CODEX_AGENT=foo codex 真实任务"},
                  sanitize=_sanitize)
    assert r.action == EventAction.ROUTE
    assert "CODEX_AGENT" not in r.text
    assert "真实任务" in r.text


def test_route_msg_id_is_preserved():
    r = _classify({"message_id": "msg-abc", "chat_id": "chat-1",
                   "text": "普通消息"})
    assert r.msg_id == "msg-abc"


def test_chat_id_empty_means_no_filter():
    """Empty chat_id disables cross-team filtering."""
    r = _classify({"message_id": "m1", "chat_id": "any-chat", "text": "hello"},
                  chat_id="")
    assert r.action == EventAction.ROUTE


# ── prefix-route cases (per-agent prefix routing) ─────────────────────────────

def _agents_prefix(text):
    """Stand-in for RouterState.parse_prefix_target with worker_cc/codex agents."""
    import re
    agents = ("manager", "worker_cc", "worker_codex")
    alt = "|".join(re.escape(a) for a in sorted(agents, key=len, reverse=True))
    pat = re.compile(rf"^\s*@?({alt})(?:\s*[:：]\s*|\s+)(.+)$",
                     re.IGNORECASE | re.DOTALL)
    m = pat.match(text)
    if not m:
        return None, text
    canon = next((a for a in agents if a.lower() == m.group(1).lower()), None)
    body = m.group(2).strip()
    if not canon or not body:
        return None, text
    return canon, body


def test_prefix_route_colon_form():
    r = _classify({"message_id": "m1", "chat_id": "chat-1",
                   "text": "worker_cc: 帮我看一下日志"},
                  agents=("manager", "worker_cc", "worker_codex"),
                  prefix_target=_agents_prefix)
    assert r.action == EventAction.ROUTE
    assert r.targets == ["worker_cc"]
    assert r.text == "帮我看一下日志"


def test_prefix_route_space_form():
    r = _classify({"message_id": "m1", "chat_id": "chat-1",
                   "text": "worker_codex 你好"},
                  agents=("manager", "worker_cc", "worker_codex"),
                  prefix_target=_agents_prefix)
    assert r.action == EventAction.ROUTE
    assert r.targets == ["worker_codex"]
    assert r.text == "你好"


def test_prefix_route_at_form_uses_at_path():
    """@worker_cc is already a hard @-mention; prefix path is unused."""
    r = _classify({"message_id": "m1", "chat_id": "chat-1",
                   "text": "@worker_cc 你好"},
                  agents=("manager", "worker_cc", "worker_codex"),
                  prefix_target=_agents_prefix)
    assert r.action == EventAction.ROUTE
    assert "worker_cc" in r.targets


def test_prefix_route_unknown_falls_through_to_manager():
    r = _classify({"message_id": "m1", "chat_id": "chat-1",
                   "text": "worker_xx 你好"},
                  agents=("manager", "worker_cc", "worker_codex"),
                  prefix_target=_agents_prefix)
    assert r.action == EventAction.ROUTE
    assert r.targets == ["manager"]


def test_prefix_route_chinese_colon():
    r = _classify({"message_id": "m1", "chat_id": "chat-1",
                   "text": "worker_cc：报到一下"},
                  agents=("manager", "worker_cc", "worker_codex"),
                  prefix_target=_agents_prefix)
    assert r.action == EventAction.ROUTE
    assert r.targets == ["worker_cc"]
    assert r.text == "报到一下"


# ── main ──────────────────────────────────────────────────────────────────────

def main() -> int:
    cases = [
        test_drop_no_msg_id,
        test_drop_dedup,
        test_drop_cross_team,
        test_drop_bot_self_message,
        test_drop_empty_text_after_sanitize,
        test_drop_agent_broadcast_no_target,
        test_slash_command_detected,
        test_slash_team_command,
        test_route_default_to_manager_for_user_message,
        test_route_at_mention_single_target,
        test_route_at_mention_multiple_targets,
        test_route_excludes_sender_from_targets,
        test_route_text_is_sanitized,
        test_route_msg_id_is_preserved,
        test_chat_id_empty_means_no_filter,
        test_prefix_route_colon_form,
        test_prefix_route_space_form,
        test_prefix_route_at_form_uses_at_path,
        test_prefix_route_unknown_falls_through_to_manager,
        test_prefix_route_chinese_colon,
    ]
    passed = failed = 0
    for fn in cases:
        try:
            fn()
            print(f"  ✅ {fn.__name__}")
            passed += 1
        except Exception as exc:
            print(f"  ❌ {fn.__name__}: {exc}")
            failed += 1
    print(f"\nrouter dispatch tests: {passed}/{passed + failed} passed")
    return 0 if failed == 0 else 1


if __name__ == "__main__":
    raise SystemExit(main())
