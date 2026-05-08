#!/usr/bin/env python3
"""Compatibility wrapper for the ClaudeTeam Feishu router daemon."""
from __future__ import annotations

import atexit
import os
import sys

_SRC_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "src")
if _SRC_DIR not in sys.path:
    sys.path.insert(0, _SRC_DIR)

from claudeteam.messaging.router.cursor import load_cursor, save_cursor  # noqa: E402
from claudeteam.messaging.router.daemon import main  # noqa: E402
from claudeteam.runtime.paths import legacy_script_state_file, runtime_state_file  # noqa: E402

PID_FILE = runtime_state_file("router.pid")
LEGACY_PID_FILE = legacy_script_state_file(".router.pid")
_LEGACY_PID_FILE = LEGACY_PID_FILE
CURSOR_FILE = runtime_state_file("router.cursor")
LEGACY_CURSOR_FILE = legacy_script_state_file(".router.cursor")
_LEGACY_CURSOR_FILE = LEGACY_CURSOR_FILE
TMUX_INTERCEPT_LOG = runtime_state_file("tmux_intercept.log")
ROUTER_MSG_DIR = runtime_state_file("router_messages")


def _load_cursor():
    return load_cursor([CURSOR_FILE, LEGACY_CURSOR_FILE])


def _advance_cursor_to(ts: float) -> None:
    save_cursor(CURSOR_FILE, ts, _load_cursor())


def _pid_file_is_live_router(path: str) -> bool:
    if not os.path.exists(path):
        return False
    try:
        with open(path) as f:
            old_pid = int(f.read().strip())
        os.kill(old_pid, 0)
        with open(f"/proc/{old_pid}/cmdline", "rb") as f:
            cmdline = f.read().decode("utf-8", errors="ignore")
        return "feishu_router.py" in cmdline or "claudeteam.messaging.router.daemon" in cmdline
    except (ValueError, OSError):
        return False


def acquire_pid_lock() -> None:
    for path in (PID_FILE, LEGACY_PID_FILE):
        if _pid_file_is_live_router(path):
            with open(path) as f:
                old_pid = f.read().strip()
            print(f"❌ Router 已在运行 (PID {old_pid})，请勿重复启动")
            sys.exit(1)
    pid = str(os.getpid())
    for path in (PID_FILE, LEGACY_PID_FILE):
        os.makedirs(os.path.dirname(path), exist_ok=True)
        with open(path, "w") as f:
            f.write(pid)
    atexit.register(_cleanup_pid)


def _cleanup_pid() -> None:
    try:
        my_pid = os.getpid()
        for path in (PID_FILE, LEGACY_PID_FILE):
            if os.path.exists(path):
                with open(path) as f:
                    pid = int(f.read().strip())
                if pid == my_pid:
                    os.remove(path)
    except Exception:
        pass


if __name__ == "__main__":
    raise SystemExit(main())
