#!/usr/bin/env python3
"""No-live compatibility checks for legacy import paths.

This gate protects Phase-1 refactor work where logic may move to src/claudeteam
but legacy imports under scripts/ must keep working.
"""
from __future__ import annotations

import contextlib
import builtins
import importlib
import io
import inspect
import os
import subprocess
import sys
import tempfile
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
SCRIPTS = ROOT / "scripts"
SRC = ROOT / "src"
for path in (SCRIPTS, SRC, ROOT):
    if path.exists() and str(path) not in sys.path:
        sys.path.insert(0, str(path))


def _normalized_signature(fn):
    sig = inspect.signature(fn)
    params = [
        p.replace(annotation=inspect.Signature.empty)
        for p in sig.parameters.values()
    ]
    return sig.replace(
        parameters=params,
        return_annotation=inspect.Signature.empty,
    )


@contextlib.contextmanager
def patched_env(name: str, value: str | None):
    old = os.environ.get(name)
    if value is None:
        os.environ.pop(name, None)
    else:
        os.environ[name] = value
    try:
        yield
    finally:
        if old is None:
            os.environ.pop(name, None)
        else:
            os.environ[name] = old


@contextlib.contextmanager
def redirected_local_facts(local_facts_module, root: Path):
    globals_dict = local_facts_module.append_message.__globals__
    names = ("FACTS_DIR", "INBOX_FILE", "STATUS_FILE", "LOG_FILE", "LOCK_FILE")
    old = {name: globals_dict[name] for name in names}
    facts_dir = root / "facts"
    updates = {
        "FACTS_DIR": facts_dir,
        "INBOX_FILE": facts_dir / "inbox.json",
        "STATUS_FILE": facts_dir / "status.json",
        "LOG_FILE": facts_dir / "logs.jsonl",
        "LOCK_FILE": facts_dir / ".facts.lock",
    }
    globals_dict.update(updates)
    for name, value in updates.items():
        # Thin wrapper mode: keep module-level aliases aligned with implementation globals.
        if hasattr(local_facts_module, name):
            setattr(local_facts_module, name, value)
    try:
        yield
    finally:
        globals_dict.update(old)
        for name, value in old.items():
            if hasattr(local_facts_module, name):
                setattr(local_facts_module, name, value)


def test_feishu_msg_lark_compat_contract() -> None:
    feishu_msg = importlib.import_module("feishu_msg")
    lark_helpers = (
        "_lark_run",
        "_check_lark_result",
        "_lark_im_send",
        "_lark_base_create",
        "_lark_base_search",
        "_lark_base_update",
        "_lark_base_list",
    )
    for name in lark_helpers:
        assert hasattr(feishu_msg, name), f"feishu_msg missing {name}"

    # Monkeypatch compatibility contract:
    # patching legacy feishu_msg._lark_run must still capture old-layer calls.
    captured = []
    old_lark_run = feishu_msg._lark_run
    try:
        def fake_lark_run(args, timeout=30):
            captured.append((list(args), timeout))
            return {"mock": "ok"}

        feishu_msg._lark_run = fake_lark_run
        out = feishu_msg._lark_base_list("base_token", "table_id", limit=7, offset=3)
    finally:
        feishu_msg._lark_run = old_lark_run

    assert captured, "patching feishu_msg._lark_run did not affect _lark_base_list"
    args, timeout = captured[0]
    assert timeout == 30
    assert args[:2] == ["base", "+record-list"], args
    assert "--base-token" in args and "base_token" in args, args
    assert "--table-id" in args and "table_id" in args, args
    assert "--limit" in args and "7" in args, args
    assert "--offset" in args and "3" in args, args
    assert out == {"mock": "ok"}

    client_file = ROOT / "src" / "claudeteam" / "integrations" / "feishu" / "client.py"
    if not client_file.exists():
        return

    client_mod = importlib.import_module("claudeteam.integrations.feishu.client")
    for name in lark_helpers:
        assert hasattr(client_mod, name), f"feishu client missing {name}"
        legacy_fn = getattr(feishu_msg, name)
        client_fn = getattr(client_mod, name)
        assert callable(legacy_fn) and callable(client_fn), f"{name} not callable"
        assert inspect.signature(legacy_fn) == inspect.signature(client_fn), (
            f"{name} signature mismatch between legacy layer and client module"
        )


def test_feishu_msg_helper_compat_contract() -> None:
    feishu_msg = importlib.import_module("feishu_msg")
    helper_names = (
        "sanitize_agent_message",
        "build_system_card",
        "build_card",
    )
    for name in helper_names:
        assert hasattr(feishu_msg, name), f"feishu_msg missing helper {name}"
        assert callable(getattr(feishu_msg, name)), f"feishu_msg helper {name} not callable"

    # C2a focus: sanitizer behavior must remain stable and no remote calls needed.
    sanitize_cases = {
        "whole line": (
            "CODEX_AGENT=toolsmith codex --dangerously-bypass-approvals-and-sandbox\n真实任务",
            "真实任务",
        ),
        "prefix with model": (
            "CODEX_AGENT=devops codex --dangerously-bypass-approvals-and-sandbox --model gpt-5.4 真实任务",
            "真实任务",
        ),
        "suffix": (
            "真实任务 CODEX_AGENT=toolsmith codex --dangerously-bypass-approvals-and-sandbox",
            "真实任务",
        ),
        "middle multiline": (
            "第一行\nCODEX_AGENT=devops codex --dangerously-bypass-approvals-and-sandbox --model gpt-5.4\n第二行",
            "第一行\n第二行",
        ),
    }
    for label, (raw, expected) in sanitize_cases.items():
        actual = feishu_msg.sanitize_agent_message(raw)
        assert actual == expected, f"{label}: expected {expected!r}, got {actual!r}"

    # build_* should stay pure/deterministic and preserve legacy output shape.
    sys_card = feishu_msg.build_system_card("CODEX_AGENT=toolsmith codex --dangerously-bypass-approvals-and-sandbox 任务")
    assert isinstance(sys_card, dict) and "header" in sys_card and "elements" in sys_card
    sys_text = (sys_card.get("elements") or [{}])[0].get("content", "")
    assert "CODEX_AGENT=" not in sys_text

    normal_card = feishu_msg.build_card("__compat_unknown_agent__", "toolsmith", "请处理")
    assert isinstance(normal_card, dict) and "header" in normal_card and "elements" in normal_card

    service_file = ROOT / "src" / "claudeteam" / "messaging" / "service.py"
    if not service_file.exists():
        return

    service_mod = importlib.import_module("claudeteam.messaging.service")
    for name in helper_names:
        assert hasattr(service_mod, name), f"messaging.service missing {name}"
        legacy_fn = getattr(feishu_msg, name)
        service_fn = getattr(service_mod, name)
        assert callable(service_fn), f"messaging.service {name} not callable"
        assert _normalized_signature(legacy_fn) == _normalized_signature(service_fn), (
            f"{name} signature mismatch between legacy layer and messaging.service"
        )


def test_feishu_msg_workspace_log_compat_contract() -> None:
    feishu_msg = importlib.import_module("feishu_msg")
    from claudeteam.storage import local_facts
    helper_names = (
        "ws_log",
        "cmd_log",
        "cmd_workspace",
    )
    for name in helper_names:
        assert hasattr(feishu_msg, name), f"feishu_msg missing helper {name}"
        assert callable(getattr(feishu_msg, name)), f"feishu_msg helper {name} not callable"

    forbidden_calls = []
    old_helpers = {}

    def _forbidden_remote(*args, **kwargs):
        forbidden_calls.append((args, kwargs))
        raise AssertionError("unexpected remote helper call during local workspace logging test")

    for name in (
        "_lark_run",
        "_lark_im_send",
        "_lark_base_create",
        "_lark_base_search",
        "_lark_base_update",
        "_lark_base_list",
    ):
        if hasattr(feishu_msg, name):
            old_helpers[name] = getattr(feishu_msg, name)
            setattr(feishu_msg, name, _forbidden_remote)

    try:
        with tempfile.TemporaryDirectory() as tmp:
            with redirected_local_facts(local_facts, Path(tmp)):
                feishu_msg.ws_log("toolsmith", "任务日志", "ws-log-entry", "REF-WS")
                logs = local_facts.list_logs("toolsmith", limit=20)
                assert any(
                    row.get("type") == "任务日志"
                    and "ws-log-entry" in row.get("content", "")
                    and row.get("ref") == "REF-WS"
                    for row in logs
                ), logs

                out_log = io.StringIO()
                with contextlib.redirect_stdout(out_log):
                    feishu_msg.cmd_log("toolsmith", "产出记录", "cmd-log-entry", "REF-CMD")
                assert "本地工作空间日志" in out_log.getvalue()
                logs = local_facts.list_logs("toolsmith", limit=50)
                assert any(
                    row.get("type") == "产出记录"
                    and "cmd-log-entry" in row.get("content", "")
                    and row.get("ref") == "REF-CMD"
                    for row in logs
                ), logs

                out_workspace = io.StringIO()
                with contextlib.redirect_stdout(out_workspace):
                    feishu_msg.cmd_workspace("toolsmith")
                workspace_text = out_workspace.getvalue()
                assert "本地工作空间日志" in workspace_text
                assert "ws-log-entry" in workspace_text
                assert "cmd-log-entry" in workspace_text

    finally:
        for name, fn in old_helpers.items():
            setattr(feishu_msg, name, fn)

    assert not forbidden_calls, f"unexpected remote helper calls: {forbidden_calls!r}"

    service_file = ROOT / "src" / "claudeteam" / "messaging" / "service.py"
    if not service_file.exists():
        return

    service_mod = importlib.import_module("claudeteam.messaging.service")
    for name in helper_names:
        assert hasattr(service_mod, name), f"messaging.service missing {name}"
        legacy_fn = getattr(feishu_msg, name)
        service_fn = getattr(service_mod, name)
        assert callable(service_fn), f"messaging.service {name} not callable"
        assert _normalized_signature(legacy_fn) == _normalized_signature(service_fn), (
            f"{name} signature mismatch between legacy layer and messaging.service"
        )


def test_feishu_msg_inbox_compat_contract() -> None:
    feishu_msg = importlib.import_module("feishu_msg")
    from claudeteam.storage import local_facts

    assert hasattr(feishu_msg, "cmd_inbox"), "feishu_msg missing helper cmd_inbox"
    assert callable(feishu_msg.cmd_inbox), "feishu_msg helper cmd_inbox not callable"

    forbidden_calls = []
    old_helpers = {}

    def _forbidden_remote(*args, **kwargs):
        forbidden_calls.append((args, kwargs))
        raise AssertionError("unexpected remote helper call during cmd_inbox compatibility test")

    for name in (
        "_lark_run",
        "_lark_im_send",
        "_lark_base_create",
        "_lark_base_search",
        "_lark_base_update",
        "_lark_base_list",
    ):
        if hasattr(feishu_msg, name):
            old_helpers[name] = getattr(feishu_msg, name)
            setattr(feishu_msg, name, _forbidden_remote)

    try:
        with tempfile.TemporaryDirectory() as tmp:
            with redirected_local_facts(local_facts, Path(tmp)):
                local_id = local_facts.append_message(
                    "toolsmith",
                    "manager",
                    "cmd_inbox 兼容门禁消息",
                    "高",
                )
                out = io.StringIO()
                with contextlib.redirect_stdout(out):
                    feishu_msg.cmd_inbox("toolsmith")
                text = out.getvalue()
                assert "有 1 条未读消息" in text, text
                assert "来自 manager" in text, text
                assert "优先级:高" in text, text
                assert "cmd_inbox 兼容门禁消息" in text, text
                assert f"python3 scripts/feishu_msg.py read {local_id}" in text, text
    finally:
        for name, fn in old_helpers.items():
            setattr(feishu_msg, name, fn)

    assert not forbidden_calls, f"unexpected remote helper calls: {forbidden_calls!r}"

    service_file = ROOT / "src" / "claudeteam" / "messaging" / "service.py"
    if not service_file.exists():
        return

    service_mod = importlib.import_module("claudeteam.messaging.service")
    assert hasattr(service_mod, "cmd_inbox"), "messaging.service missing cmd_inbox"
    assert callable(service_mod.cmd_inbox), "messaging.service cmd_inbox not callable"
    assert _normalized_signature(feishu_msg.cmd_inbox) == _normalized_signature(service_mod.cmd_inbox), (
        "cmd_inbox signature mismatch between legacy layer and messaging.service"
    )


def test_feishu_msg_read_compat_contract() -> None:
    feishu_msg = importlib.import_module("feishu_msg")
    from claudeteam.storage import local_facts

    assert hasattr(feishu_msg, "cmd_read"), "feishu_msg missing helper cmd_read"
    assert callable(feishu_msg.cmd_read), "feishu_msg helper cmd_read not callable"

    forbidden_calls = []
    old_helpers = {}

    def _forbidden_remote(*args, **kwargs):
        forbidden_calls.append((args, kwargs))
        raise AssertionError("unexpected remote helper call during cmd_read compatibility test")

    for name in (
        "_lark_run",
        "_lark_im_send",
        "_lark_base_create",
        "_lark_base_search",
        "_lark_base_update",
        "_lark_base_list",
    ):
        if hasattr(feishu_msg, name):
            old_helpers[name] = getattr(feishu_msg, name)
            setattr(feishu_msg, name, _forbidden_remote)

    try:
        with tempfile.TemporaryDirectory() as tmp:
            with redirected_local_facts(local_facts, Path(tmp)):
                local_id = local_facts.append_message(
                    "toolsmith",
                    "manager",
                    "cmd_read 兼容门禁消息",
                    "高",
                )
                out = io.StringIO()
                with contextlib.redirect_stdout(out):
                    feishu_msg.cmd_read(local_id)
                text = out.getvalue()
                assert "已标记本地已读" in text, text
                unread = local_facts.list_messages("toolsmith", unread_only=True)
                assert all(rec.get("local_id") != local_id for rec in unread), unread
    finally:
        for name, fn in old_helpers.items():
            setattr(feishu_msg, name, fn)

    assert not forbidden_calls, f"unexpected remote helper calls: {forbidden_calls!r}"

    service_file = ROOT / "src" / "claudeteam" / "messaging" / "service.py"
    if not service_file.exists():
        return

    service_mod = importlib.import_module("claudeteam.messaging.service")
    for name in ("cmd_read", "cmd_read_local", "read_local_message", "mark_read_local"):
        if hasattr(service_mod, name):
            assert callable(getattr(service_mod, name)), f"messaging.service {name} not callable"


def test_feishu_msg_status_compat_contract() -> None:
    feishu_msg = importlib.import_module("feishu_msg")
    from claudeteam.storage import local_facts

    assert hasattr(feishu_msg, "cmd_status"), "feishu_msg missing helper cmd_status"
    assert callable(feishu_msg.cmd_status), "feishu_msg helper cmd_status not callable"

    forbidden_calls = []
    old_helpers = {}

    def _forbidden_remote(*args, **kwargs):
        forbidden_calls.append((args, kwargs))
        raise AssertionError("unexpected remote helper call during cmd_status compatibility test")

    for name in (
        "_lark_run",
        "_lark_im_send",
        "_lark_base_create",
        "_lark_base_search",
        "_lark_base_update",
        "_lark_base_list",
    ):
        if hasattr(feishu_msg, name):
            old_helpers[name] = getattr(feishu_msg, name)
            setattr(feishu_msg, name, _forbidden_remote)

    try:
        with tempfile.TemporaryDirectory() as tmp:
            with redirected_local_facts(local_facts, Path(tmp)):
                out = io.StringIO()
                with contextlib.redirect_stdout(out):
                    feishu_msg.cmd_status("toolsmith", "进行中", "状态门禁任务", "")
                text = out.getvalue()
                assert (
                    ("toolsmith" in text and "进行中" in text and "状态门禁任务" in text)
                    or "local-only" in text
                ), text

                status = local_facts.get_status("toolsmith")
                assert status, "local status not written by cmd_status"
                assert status.get("status") == "进行中", status
                assert status.get("task") == "状态门禁任务", status
    finally:
        for name, fn in old_helpers.items():
            setattr(feishu_msg, name, fn)

    assert not forbidden_calls, f"unexpected remote helper calls: {forbidden_calls!r}"

    service_file = ROOT / "src" / "claudeteam" / "messaging" / "service.py"
    if not service_file.exists():
        return

    service_mod = importlib.import_module("claudeteam.messaging.service")
    for name in ("upsert_local_status", "cmd_status_local", "status_local"):
        if hasattr(service_mod, name):
            assert callable(getattr(service_mod, name)), f"messaging.service {name} not callable"


def test_feishu_msg_send_local_persistence_compat_contract() -> None:
    feishu_msg = importlib.import_module("feishu_msg")
    from claudeteam.storage import local_facts

    assert hasattr(feishu_msg, "cmd_send"), "feishu_msg missing helper cmd_send"
    assert callable(feishu_msg.cmd_send), "feishu_msg helper cmd_send not callable"

    forbidden_calls = {}
    old_helpers = {}

    def _forbidden_factory(name):
        def _forbidden(*args, **kwargs):
            forbidden_calls.setdefault(name, []).append((args, kwargs))
            raise AssertionError(f"unexpected call to {name} during cmd_send local-only compatibility test")

        return _forbidden

    for name in (
        "_lark_run",
        "_lark_im_send",
        "_lark_base_create",
        "_lark_base_search",
        "_lark_base_update",
        "_lark_base_list",
        "post_to_group",
        "_project_message_to_bitable",
    ):
        if hasattr(feishu_msg, name):
            old_helpers[name] = getattr(feishu_msg, name)
            setattr(feishu_msg, name, _forbidden_factory(name))

    notify_calls = []
    old_notify = getattr(feishu_msg, "_notify_agent_tmux", None)

    def _capture_notify(to_agent, from_agent, message):
        notify_calls.append((to_agent, from_agent, message))
        return True

    if old_notify is not None:
        feishu_msg._notify_agent_tmux = _capture_notify

    try:
        with tempfile.TemporaryDirectory() as tmp:
            with redirected_local_facts(local_facts, Path(tmp)), patched_env(
                "CLAUDETEAM_ENABLE_BITABLE_LEGACY", ""
            ), patched_env("CLAUDETEAM_ENABLE_FEISHU_REMOTE", ""):
                out = io.StringIO()
                with contextlib.redirect_stdout(out):
                    feishu_msg.cmd_send("toolsmith", "manager", "send门禁消息", "高", "TASK-LOCAL")
                text = out.getvalue()
                assert "local_id:" in text, text
                assert "local-only" in text, text

                unread = local_facts.list_messages("toolsmith", unread_only=True)
                assert unread, "cmd_send did not persist local inbox message"
                rec = unread[-1]
                assert rec.get("to") == "toolsmith", rec
                assert rec.get("from") == "manager", rec
                assert rec.get("priority") == "高", rec
                assert rec.get("task_id") == "TASK-LOCAL", rec
                assert rec.get("content") == "[TASK-LOCAL] send门禁消息", rec
                assert notify_calls, "cmd_send did not invoke _notify_agent_tmux"
                assert notify_calls[-1] == (
                    "toolsmith",
                    "manager",
                    "[TASK-LOCAL] send门禁消息",
                ), notify_calls
    finally:
        for name, fn in old_helpers.items():
            setattr(feishu_msg, name, fn)
        if old_notify is not None:
            feishu_msg._notify_agent_tmux = old_notify

    assert not forbidden_calls, f"unexpected remote/projection calls: {forbidden_calls!r}"

    service_file = ROOT / "src" / "claudeteam" / "messaging" / "service.py"
    if not service_file.exists():
        return

    service_mod = importlib.import_module("claudeteam.messaging.service")
    for name in ("record_local_send", "append_local_message", "cmd_send_local"):
        if hasattr(service_mod, name):
            assert callable(getattr(service_mod, name)), f"messaging.service {name} not callable"


def test_feishu_msg_direct_local_persistence_compat_contract() -> None:
    feishu_msg = importlib.import_module("feishu_msg")
    from claudeteam.storage import local_facts

    assert hasattr(feishu_msg, "cmd_direct"), "feishu_msg missing helper cmd_direct"
    assert callable(feishu_msg.cmd_direct), "feishu_msg helper cmd_direct not callable"

    forbidden_calls = {}
    old_helpers = {}

    def _forbidden_factory(name):
        def _forbidden(*args, **kwargs):
            forbidden_calls.setdefault(name, []).append((args, kwargs))
            raise AssertionError(f"unexpected call to {name} during cmd_direct local-only compatibility test")

        return _forbidden

    for name in (
        "_lark_run",
        "_lark_im_send",
        "_lark_base_create",
        "_lark_base_search",
        "_lark_base_update",
        "_lark_base_list",
        "_project_message_to_bitable",
    ):
        if hasattr(feishu_msg, name):
            old_helpers[name] = getattr(feishu_msg, name)
            setattr(feishu_msg, name, _forbidden_factory(name))

    notify_calls = []
    old_notify = getattr(feishu_msg, "_notify_agent_tmux", None)

    def _capture_notify(to_agent, from_agent, message):
        notify_calls.append((to_agent, from_agent, message))
        return True

    if old_notify is not None:
        feishu_msg._notify_agent_tmux = _capture_notify

    try:
        with tempfile.TemporaryDirectory() as tmp:
            with redirected_local_facts(local_facts, Path(tmp)), patched_env(
                "CLAUDETEAM_ENABLE_BITABLE_LEGACY", ""
            ), patched_env("CLAUDETEAM_ENABLE_FEISHU_REMOTE", ""):
                out = io.StringIO()
                with contextlib.redirect_stdout(out):
                    feishu_msg.cmd_direct("toolsmith", "coder", "direct门禁消息")
                text = out.getvalue()
                assert "local_id:" in text, text
                assert "local-only" in text, text

                toolsmith_unread = local_facts.list_messages("toolsmith", unread_only=True)
                assert toolsmith_unread, "cmd_direct did not persist local inbox message for toolsmith"
                msg_rec = toolsmith_unread[-1]
                assert msg_rec.get("to") == "toolsmith", msg_rec
                assert msg_rec.get("from") == "coder", msg_rec
                assert msg_rec.get("content") == "direct门禁消息", msg_rec
                assert msg_rec.get("priority") == "中", msg_rec

                manager_unread = local_facts.list_messages("manager", unread_only=True)
                assert manager_unread, "cmd_direct did not persist cc message for manager"
                cc_rec = manager_unread[-1]
                assert cc_rec.get("to") == "manager", cc_rec
                assert cc_rec.get("from") == "coder", cc_rec
                assert cc_rec.get("priority") == "低", cc_rec
                assert cc_rec.get("content") == "[抄送] coder→toolsmith: direct门禁消息", cc_rec

                assert notify_calls, "cmd_direct did not invoke _notify_agent_tmux"
                assert notify_calls[-1] == ("toolsmith", "coder", "direct门禁消息"), notify_calls
    finally:
        for name, fn in old_helpers.items():
            setattr(feishu_msg, name, fn)
        if old_notify is not None:
            feishu_msg._notify_agent_tmux = old_notify

    assert not forbidden_calls, f"unexpected remote/projection calls: {forbidden_calls!r}"

    service_file = ROOT / "src" / "claudeteam" / "messaging" / "service.py"
    if not service_file.exists():
        return

    service_mod = importlib.import_module("claudeteam.messaging.service")
    for name in ("record_local_direct", "append_local_direct", "cmd_direct_local"):
        if hasattr(service_mod, name):
            assert callable(getattr(service_mod, name)), f"messaging.service {name} not callable"


def test_kanban_projection_compat_contract() -> None:
    kanban_sync = importlib.import_module("kanban_sync")
    legacy_names = (
        "cmd_init",
        "cmd_sync",
        "cmd_daemon",
        "do_sync",
        "_lark",
        "load_tasks",
        "load_cfg",
        "save_cfg",
    )
    for name in legacy_names:
        assert hasattr(kanban_sync, name), f"kanban_sync missing {name}"
        assert callable(getattr(kanban_sync, name)), f"kanban_sync {name} not callable"

    projection_file = ROOT / "src" / "claudeteam" / "integrations" / "feishu" / "kanban_projection.py"
    if not projection_file.exists():
        return

    blocked_imports = ("kanban_sync", "scripts.kanban_sync")
    orig_import = builtins.__import__
    orig_subprocess_run = subprocess.run
    orig_os_kill = os.kill
    orig_open = builtins.open
    side_effect_calls = []

    sys.modules.pop("claudeteam.integrations.feishu.kanban_projection", None)
    for name in blocked_imports:
        sys.modules.pop(name, None)

    def _guarded_import(name, globals=None, locals=None, fromlist=(), level=0):
        if name in blocked_imports or any(name.startswith(prefix + ".") for prefix in blocked_imports):
            raise AssertionError(f"forbidden import in kanban projection gate: {name}")
        return orig_import(name, globals, locals, fromlist, level)

    def _forbidden_subprocess(*args, **kwargs):
        side_effect_calls.append(("subprocess.run", args, kwargs))
        raise AssertionError("kanban projection gate forbids subprocess.run")

    def _forbidden_kill(*args, **kwargs):
        side_effect_calls.append(("os.kill", args, kwargs))
        raise AssertionError("kanban projection gate forbids os.kill")

    def _forbidden_open(*args, **kwargs):
        side_effect_calls.append(("open", args, kwargs))
        raise AssertionError("kanban projection gate forbids file I/O")

    builtins.__import__ = _guarded_import
    subprocess.run = _forbidden_subprocess
    os.kill = _forbidden_kill
    builtins.open = _forbidden_open
    try:
        projection = importlib.import_module("claudeteam.integrations.feishu.kanban_projection")
    finally:
        builtins.__import__ = orig_import
        subprocess.run = orig_subprocess_run
        os.kill = orig_os_kill
        builtins.open = orig_open

    for name in blocked_imports:
        assert name not in sys.modules, f"kanban projection imported forbidden module: {name}"

    for name in (
        "extract_text",
        "to_ms",
        "chunks",
        "build_kanban_table_fields",
        "extract_kanban_record_ids",
        "build_agent_status_map",
        "build_kanban_rows",
    ):
        assert hasattr(projection, name), f"kanban_projection missing {name}"
        assert callable(getattr(projection, name)), f"kanban_projection {name} not callable"
    assert hasattr(projection, "KANBAN_FIELD_NAMES"), "kanban_projection missing KANBAN_FIELD_NAMES"
    assert hasattr(projection, "KANBAN_TABLE_FIELDS"), "kanban_projection missing KANBAN_TABLE_FIELDS"

    expected_fields = ["任务ID", "标题", "状态", "负责人", "Agent当前状态", "Agent当前任务"]
    assert list(projection.KANBAN_FIELD_NAMES) == expected_fields, projection.KANBAN_FIELD_NAMES

    expected_table_fields = [
        {"name": "任务ID", "type": "text"},
        {"name": "标题", "type": "text"},
        {"name": "状态", "type": "text"},
        {"name": "负责人", "type": "text"},
        {"name": "Agent当前状态", "type": "text"},
        {"name": "Agent当前任务", "type": "text"},
        {"name": "任务更新时间", "type": "date_time"},
        {"name": "Agent状态更新", "type": "date_time"},
    ]
    assert list(projection.KANBAN_TABLE_FIELDS) == expected_table_fields, projection.KANBAN_TABLE_FIELDS

    built_table_fields = projection.build_kanban_table_fields()
    assert built_table_fields == expected_table_fields, built_table_fields
    assert len(built_table_fields) == 8, built_table_fields
    assert any(row.get("name") == "任务更新时间" for row in built_table_fields), built_table_fields
    assert any(row.get("name") == "Agent状态更新" for row in built_table_fields), built_table_fields
    built_table_fields[0]["name"] = "__mutated__"
    assert list(projection.KANBAN_TABLE_FIELDS) == expected_table_fields, projection.KANBAN_TABLE_FIELDS

    tasks = [
        {"task_id": "TASK-1", "title": "任务一", "status": "进行中", "assignee": "toolsmith"},
        {"task_id": "TASK-2", "title": "任务二", "status": "阻塞", "assignee": "architect"},
        {"task_id": "TASK-3", "title": "任务三", "status": "待办", "assignee": "nobody"},
    ]
    agent_status = {
        "toolsmith": {"状态": "进行中", "当前任务": "处理门禁"},
        "architect": {"状态": "待命"},
    }

    builtins.open = _forbidden_open
    try:
        record_items = [
            {"record_id": "rec_001", "fields": {"标题": [{"text": "a"}]}},
            {"record_id": "rec_002"},
            {"record_id": "rec_001", "fields": {"标题": [{"text": "dup"}]}},
        ]
        assert projection.extract_kanban_record_ids(record_items) == [
            "rec_001",
            "rec_002",
            "rec_001",
        ]
        assert projection.extract_kanban_record_ids([]) == []

        status_items = [
            {
                "fields": {
                    "Agent名称": [{"text": "toolsmith"}],
                    "状态": [{"text": "进行中"}],
                    "当前任务": [{"text": "状态门禁任务"}],
                    "更新时间": [{"value": 1710001111000}, {"value": 1710001111999}],
                }
            },
            {
                "fields": {
                    "Agent名称": "toolsmith",
                    "状态": "应被忽略",
                    "当前任务": "重复数据",
                    "更新时间": [{"value": 1710001112999}],
                }
            },
            {
                "fields": {
                    "Agent名称": [{"text": "architect"}],
                    "更新时间": [{"value": 1710002222000}],
                }
            },
            {
                "fields": {
                    "Agent名称": [],
                    "状态": "空Agent应跳过",
                    "当前任务": "skip",
                    "更新时间": [{"value": 1710003333000}],
                }
            },
        ]
        status_map = projection.build_agent_status_map(status_items)
        assert status_map == {
            "toolsmith": {
                "状态": "进行中",
                "当前任务": "状态门禁任务",
                "更新时间": 1710001111000,
            },
            "architect": {
                "状态": "",
                "当前任务": "",
                "更新时间": 1710002222000,
            },
        }, status_map

        result = projection.build_kanban_rows(tasks, agent_status)
    finally:
        builtins.open = orig_open

    if isinstance(result, tuple) and len(result) == 2:
        fields, rows = result
    elif isinstance(result, dict):
        fields = result.get("fields", projection.KANBAN_FIELD_NAMES)
        rows = result.get("rows", result.get("data"))
    else:
        fields = projection.KANBAN_FIELD_NAMES
        rows = result

    assert list(fields) == expected_fields, fields
    expected_rows = [
        ["TASK-1", "任务一", "进行中", "toolsmith", "进行中", "处理门禁"],
        ["TASK-2", "任务二", "阻塞", "architect", "待命", ""],
        ["TASK-3", "任务三", "待办", "nobody", "未知", ""],
    ]
    assert rows == expected_rows, rows
    assert not side_effect_calls, f"unexpected side-effect in kanban projection gate: {side_effect_calls!r}"


def test_optional_src_module_paths_match_when_present() -> None:
    pass  # All compat shims removed; mapping is now empty


def main() -> int:
    test_feishu_msg_lark_compat_contract()
    test_feishu_msg_helper_compat_contract()
    test_feishu_msg_workspace_log_compat_contract()
    test_feishu_msg_inbox_compat_contract()
    test_feishu_msg_read_compat_contract()
    test_feishu_msg_status_compat_contract()
    test_feishu_msg_send_local_persistence_compat_contract()
    test_feishu_msg_direct_local_persistence_compat_contract()
    test_kanban_projection_compat_contract()
    test_optional_src_module_paths_match_when_present()
    print("OK: compat import paths passed")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
