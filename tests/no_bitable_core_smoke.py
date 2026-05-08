#!/usr/bin/env python3
"""No-live smoke for the default local core path.

This test exercises the real default command functions. It does not monkeypatch
Feishu/Bitable helpers. `tests/no_live_guard.py` blocks npx, @larksuite/cli,
lark-cli, tmux, network, Docker, and credential env so any leaked live/default
remote call fails the smoke.
"""
from __future__ import annotations

import contextlib
import inspect
import io
import os
import tempfile
from pathlib import Path
import sys


ROOT = Path(__file__).resolve().parents[1]
SCRIPTS = ROOT / "scripts"
TESTS = ROOT / "tests"
for path in (SCRIPTS, TESTS, ROOT):
    if str(path) not in sys.path:
        sys.path.insert(0, str(path))

from no_live_guard import install  # noqa: E402
import feishu_msg  # noqa: E402
from claudeteam.storage import local_facts  # noqa: E402
import claudeteam.commands.task_tracker as task_tracker  # noqa: E402


REMOTE_ENV = (
    feishu_msg.LEGACY_BITABLE_ENV,
    feishu_msg.FEISHU_REMOTE_ENV,
    "FEISHU_APP_ID",
    "FEISHU_APP_SECRET",
    "LARK_APP_ID",
    "LARK_APP_SECRET",
    "LARK_CLI_PROFILE",
)


def _redirect_facts(tmp: str) -> None:
    local_facts.FACTS_DIR = local_facts.Path(tmp) / "facts"
    local_facts.INBOX_FILE = local_facts.FACTS_DIR / "inbox.json"
    local_facts.STATUS_FILE = local_facts.FACTS_DIR / "status.json"
    local_facts.LOG_FILE = local_facts.FACTS_DIR / "logs.jsonl"
    local_facts.LOCK_FILE = local_facts.FACTS_DIR / ".facts.lock"


@contextlib.contextmanager
def no_remote_env():
    old = {name: os.environ.get(name) for name in REMOTE_ENV}
    for name in REMOTE_ENV:
        os.environ.pop(name, None)
    try:
        yield
    finally:
        for name, value in old.items():
            if value is None:
                os.environ.pop(name, None)
            else:
                os.environ[name] = value


def capture(fn, *args):
    out = io.StringIO()
    err = io.StringIO()
    with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
        fn(*args)
    return out.getvalue(), err.getvalue()


def assert_legacy_gate(fn, call_token: str) -> None:
    src = inspect.getsource(fn)
    gate = "if legacy_bitable_enabled():"
    gate_pos = src.find(gate)
    call_pos = src.find(call_token)
    assert call_pos >= 0, f"{fn.__name__} missing expected legacy call token"
    assert gate_pos >= 0 and gate_pos < call_pos, (
        f"{fn.__name__} calls {call_token} without a preceding legacy gate"
    )


def assert_no_tokens(fn, forbidden: tuple[str, ...]) -> None:
    src = inspect.getsource(fn)
    for token in forbidden:
        assert token not in src, f"{fn.__name__} default core contains {token}"


def test_static_default_remote_boundaries():
    assert_legacy_gate(feishu_msg.cmd_send, "_project_message_to_bitable(")
    assert_legacy_gate(feishu_msg.cmd_direct, "_project_message_to_bitable(")
    assert_legacy_gate(feishu_msg.cmd_status, "_search_records(")
    assert_legacy_gate(feishu_msg.cmd_read, "_lark_base_update(")

    assert_no_tokens(
        feishu_msg.cmd_log,
        ("_lark", "BT(", "MT(", "ST(", "WS(", "_project_message_to_bitable"),
    )
    assert_no_tokens(
        feishu_msg.cmd_workspace,
        ("_lark", "BT(", "MT(", "ST(", "WS(", "_project_message_to_bitable"),
    )

    post_src = inspect.getsource(feishu_msg.post_to_group)
    say_src = inspect.getsource(feishu_msg.cmd_say)
    assert "if not feishu_remote_enabled():" in post_src
    assert "if not feishu_remote_enabled():" in say_src


def test_send_inbox_read_status_log_workspace_real_default_path():
    out, _err = capture(
        feishu_msg.cmd_send,
        "devops",
        "manager",
        "TASK-021 no-bitable core smoke",
        "高",
    )
    assert "local-only" in out, out

    messages = local_facts.list_messages("devops", unread_only=True)
    assert len(messages) == 1, messages
    msg_id = messages[0]["local_id"]

    inbox_out, _ = capture(feishu_msg.cmd_inbox, "devops")
    assert msg_id in inbox_out
    assert "TASK-021 no-bitable core smoke" in inbox_out

    read_out, _ = capture(feishu_msg.cmd_read, msg_id)
    assert "本地已读" in read_out
    assert local_facts.list_messages("devops", unread_only=True) == []

    status_out, _ = capture(feishu_msg.cmd_status, "devops", "进行中", "本地事实源验收", "")
    assert "local-only" in status_out, status_out
    assert local_facts.get_status("devops")["task"] == "本地事实源验收"

    log_out, _ = capture(feishu_msg.cmd_log, "devops", "产出记录", "no_bitable_core_smoke", "TASK-021")
    assert "本地工作空间日志" in log_out

    workspace_out, _ = capture(feishu_msg.cmd_workspace, "devops")
    assert "no_bitable_core_smoke" in workspace_out


def test_direct_and_say_real_default_path():
    direct_out, _ = capture(
        feishu_msg.cmd_direct,
        "toolsmith",
        "devops",
        "默认直连只写本地 inbox",
    )
    assert "local-only" in direct_out, direct_out
    assert local_facts.list_messages("toolsmith", unread_only=True)
    assert local_facts.list_messages("manager", unread_only=True)

    out = io.StringIO()
    with contextlib.redirect_stdout(out):
        try:
            feishu_msg.cmd_say("devops", "默认 say 不允许远端发送")
        except SystemExit as exc:
            assert exc.code == 1
        else:
            raise AssertionError("cmd_say should exit when remote Feishu is disabled")
    assert "远端发送默认关闭" in out.getvalue()


def test_task_facts_and_local_kanban_source_are_files():
    old_tasks_file = task_tracker.TASKS_FILE
    try:
        task_tracker.TASKS_FILE = str(local_facts.FACTS_DIR.parent / "tasks.json")
        created_out, _ = capture(
            task_tracker.cmd_create,
            "devops",
            "本地任务事实源",
            "不依赖 Bitable/lark-cli",
            "manager",
        )
        assert "TASK-001" in created_out
        capture(task_tracker.cmd_update, "TASK-001", "进行中", None, None)

        board_out, _ = capture(task_tracker.cmd_list, None, None)
        assert "TASK-001" in board_out
        assert "本地任务事实源" in board_out
        assert "进行中" in board_out
    finally:
        task_tracker.TASKS_FILE = old_tasks_file


def main() -> int:
    with tempfile.TemporaryDirectory() as tmp, no_remote_env():
        tmp_path = Path(tmp)
        old_state_dir = os.environ.get("CLAUDETEAM_STATE_DIR")
        old_pending_dir = os.environ.get("CLAUDETEAM_PENDING_DIR")
        os.environ["CLAUDETEAM_STATE_DIR"] = str(tmp_path / "state")
        os.environ["CLAUDETEAM_PENDING_DIR"] = str(
            tmp_path / "state" / "queue" / "pending_msgs"
        )
        install()
        try:
            _redirect_facts(tmp)
            test_static_default_remote_boundaries()
            test_send_inbox_read_status_log_workspace_real_default_path()
            test_direct_and_say_real_default_path()
            test_task_facts_and_local_kanban_source_are_files()
        finally:
            if old_state_dir is None:
                os.environ.pop("CLAUDETEAM_STATE_DIR", None)
            else:
                os.environ["CLAUDETEAM_STATE_DIR"] = old_state_dir
            if old_pending_dir is None:
                os.environ.pop("CLAUDETEAM_PENDING_DIR", None)
            else:
                os.environ["CLAUDETEAM_PENDING_DIR"] = old_pending_dir
    print("OK: no_bitable_core_smoke passed")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
