#!/usr/bin/env python3
"""No-live regression for local inbox/status facts.

The checks prove Bitable failure does not drop core messages or make status
commands pretend the remote projection succeeded.
"""
from __future__ import annotations

import contextlib
import io
import os
import sys
import tempfile
from pathlib import Path

_ROOT = Path(__file__).resolve().parents[1]
for _p in (_ROOT / "scripts", _ROOT / "src", _ROOT):
    if str(_p) not in sys.path:
        sys.path.insert(0, str(_p))

import feishu_msg
from claudeteam.storage import local_facts


@contextlib.contextmanager
def _redirected_facts(tmp: str):
    """Redirect local_facts globals for both direct module and thin-wrapper mode."""
    globals_dict = local_facts.append_message.__globals__
    names = ("FACTS_DIR", "INBOX_FILE", "STATUS_FILE", "LOG_FILE", "LOCK_FILE")
    old = {name: globals_dict[name] for name in names}
    facts_dir = Path(tmp)
    updates = {
        "FACTS_DIR": facts_dir,
        "INBOX_FILE": facts_dir / "inbox.json",
        "STATUS_FILE": facts_dir / "status.json",
        "LOG_FILE": facts_dir / "logs.jsonl",
        "LOCK_FILE": facts_dir / ".facts.lock",
    }
    globals_dict.update(updates)
    for name, value in updates.items():
        if hasattr(local_facts, name):
            setattr(local_facts, name, value)
    try:
        yield
    finally:
        globals_dict.update(old)
        for name, value in old.items():
            if hasattr(local_facts, name):
                setattr(local_facts, name, value)


def test_send_survives_bitable_projection_failure():
    captured = []
    old = (
        feishu_msg.bitable_insert_message,
        feishu_msg.post_to_group,
        feishu_msg.ws_log,
        feishu_msg._notify_agent_tmux,
    )
    try:
        feishu_msg.bitable_insert_message = lambda *a, **kw: None
        feishu_msg.post_to_group = lambda *a, **kw: True
        feishu_msg.ws_log = lambda *a, **kw: None
        feishu_msg._notify_agent_tmux = (
            lambda to, frm, content:
            captured.append((to, frm, content))
        )
        feishu_msg.cmd_send("devops", "manager", "完整消息" * 600, "高")
    finally:
        (
            feishu_msg.bitable_insert_message,
            feishu_msg.post_to_group,
            feishu_msg.ws_log,
            feishu_msg._notify_agent_tmux,
        ) = old

    rows = local_facts.list_messages("devops")
    assert len(rows) == 1, rows
    assert rows[0]["content"] == "完整消息" * 600
    assert rows[0]["priority"] == "高"
    assert captured and captured[0][0] == "devops"


def test_inbox_and_read_use_local_facts():
    local_id = local_facts.append_message("coder", "manager", "本地事实源消息", "中")

    out = io.StringIO()
    with contextlib.redirect_stdout(out):
        feishu_msg.cmd_inbox("coder")
    assert local_id in out.getvalue()
    assert "本地事实源消息" in out.getvalue()

    feishu_msg.cmd_read(local_id)
    assert local_facts.list_messages("coder", unread_only=True) == []


def test_status_survives_bitable_projection_failure():
    old = (
        feishu_msg._search_records,
        feishu_msg._lark_base_update,
        feishu_msg._lark_base_create,
        feishu_msg.ws_log,
    )
    try:
        feishu_msg._search_records = lambda *a, **kw: None
        feishu_msg._lark_base_update = lambda *a, **kw: None
        feishu_msg._lark_base_create = lambda *a, **kw: None
        feishu_msg.ws_log = lambda *a, **kw: None
        feishu_msg.cmd_status("coder", "进行中", "本地状态测试", "")
    finally:
        (
            feishu_msg._search_records,
            feishu_msg._lark_base_update,
            feishu_msg._lark_base_create,
            feishu_msg.ws_log,
        ) = old

    status = local_facts.get_status("coder")
    assert status
    assert status["status"] == "进行中"
    assert status["task"] == "本地状态测试"


def main() -> int:
    with tempfile.TemporaryDirectory() as tmp:
        old_env = os.environ.get("CLAUDETEAM_FACTS_DIR")
        os.environ["CLAUDETEAM_FACTS_DIR"] = tmp
        try:
            with _redirected_facts(tmp):
                test_send_survives_bitable_projection_failure()
                test_inbox_and_read_use_local_facts()
                test_status_survives_bitable_projection_failure()
        finally:
            if old_env is None:
                os.environ.pop("CLAUDETEAM_FACTS_DIR", None)
            else:
                os.environ["CLAUDETEAM_FACTS_DIR"] = old_env
    print("✅ local facts regression passed")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
