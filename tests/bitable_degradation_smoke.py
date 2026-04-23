#!/usr/bin/env python3
"""No-live smoke checks for Bitable/kanban degraded behavior.

These checks exercise failure contracts without real Feishu credentials. They
monkeypatch `kanban_sync._lark` and local task loading, then verify that the
kanban projection skips unsafe writes when Bitable is disconnected, rate
limited, or disabled.
"""
from __future__ import annotations

import contextlib
import io
import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
SCRIPTS = ROOT / "scripts"
if str(SCRIPTS) not in sys.path:
    sys.path.insert(0, str(SCRIPTS))

import kanban_sync  # noqa: E402


CFG = {
    "bitable_app_token": "base_no_live",
    "sta_table_id": "tbl_status",
    "kanban_table_id": "tbl_kanban",
}

TASKS = {
    "tasks": [
        {
            "task_id": "TASK-NO-LIVE",
            "title": "No-live Bitable degradation smoke",
            "status": "进行中",
            "assignee": "devops",
        }
    ]
}


class Patch:
    def __init__(self, **items):
        self.items = items
        self.old = {}

    def __enter__(self):
        for name, value in self.items.items():
            self.old[name] = getattr(kanban_sync, name)
            setattr(kanban_sync, name, value)

    def __exit__(self, exc_type, exc, tb):
        for name, value in self.old.items():
            setattr(kanban_sync, name, value)


def _status_ok():
    return {
        "items": [
            {
                "record_id": "rec_status",
                "fields": {
                    "Agent名称": "devops",
                    "状态": "进行中",
                    "当前任务": "no-live smoke",
                    "更新时间": 1770000000000,
                },
            }
        ]
    }


def _run_sync(fake_lark):
    calls = []

    def wrapped(args, label="", timeout=30):
        calls.append(label)
        return fake_lark(args, label, timeout)

    out = io.StringIO()
    err = io.StringIO()
    with Patch(_lark=wrapped, load_tasks=lambda: TASKS):
        with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
            kanban_sync.do_sync(dict(CFG))
    return calls, out.getvalue(), err.getvalue()


def test_bitable_disconnected_skips_projection():
    calls, out, err = _run_sync(
        lambda _args, label, _timeout: None if label == "拉取状态表" else {}
    )

    assert calls == ["拉取状态表"], calls
    assert "跳过本轮(状态表查询失败)" in out
    assert "状态表查询失败" in err


def test_record_list_rate_limited_skips_delete_and_create():
    def fake_lark(_args, label, _timeout):
        if label == "拉取状态表":
            return _status_ok()
        if label == "获取看板记录":
            return None
        raise AssertionError(f"unexpected Bitable call after list failure: {label}")

    calls, out, err = _run_sync(fake_lark)

    assert calls == ["拉取状态表", "获取看板记录"], calls
    assert "跳过本轮看板写入" in out
    assert "获取看板记录列表失败" in err


def test_batch_delete_rate_limited_preserves_old_projection():
    def fake_lark(_args, label, _timeout):
        if label == "拉取状态表":
            return _status_ok()
        if label == "获取看板记录":
            return {"items": [{"record_id": "rec_old"}]}
        if label.startswith("批删记录"):
            return None
        raise AssertionError(f"unexpected Bitable call after delete failure: {label}")

    calls, out, _err = _run_sync(fake_lark)

    assert calls == ["拉取状态表", "获取看板记录", "批删记录 1-1/1"], calls
    assert "跳过本轮看板写入(删除失败,保留旧状态)" in out


def test_batch_create_rate_limited_stops_current_round():
    def fake_lark(_args, label, _timeout):
        if label == "拉取状态表":
            return _status_ok()
        if label == "获取看板记录":
            return {"items": []}
        if label == "批量写入看板":
            return None
        raise AssertionError(f"unexpected Bitable call: {label}")

    calls, out, err = _run_sync(fake_lark)

    assert calls == ["拉取状态表", "获取看板记录", "批量写入看板"], calls
    assert "看板部分写入失败" in out
    assert "看板批写失败" in err


def test_kanban_disabled_fails_loudly_without_live_access():
    old = kanban_sync.load_cfg
    out = io.StringIO()
    try:
        kanban_sync.load_cfg = lambda: {"bitable_app_token": "base_no_live"}
        with contextlib.redirect_stdout(out):
            try:
                kanban_sync.cmd_sync()
            except SystemExit as exc:
                assert exc.code == 1
            else:
                raise AssertionError("cmd_sync should exit when kanban is disabled")
    finally:
        kanban_sync.load_cfg = old

    assert "未找到 kanban_table_id" in out.getvalue()


def main() -> int:
    test_bitable_disconnected_skips_projection()
    test_record_list_rate_limited_skips_delete_and_create()
    test_batch_delete_rate_limited_preserves_old_projection()
    test_batch_create_rate_limited_stops_current_round()
    test_kanban_disabled_fails_loudly_without_live_access()
    print("OK: no-live Bitable degradation smoke passed")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
