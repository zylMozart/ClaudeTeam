"""Cursor and heartbeat helpers for the router daemon.

Cursor file serves two purposes simultaneously:
  mtime  — last time any event arrived from the WebSocket (watchdog heartbeat)
  content — last successfully-routed message's wall-clock timestamp (replay cursor)

Pure functions: parse_cursor, parse_create_time
I/O wrappers: load_cursor, save_cursor, refresh_heartbeat
"""
from __future__ import annotations

import os
import re
import time
from datetime import datetime
from typing import Optional


def parse_cursor(content: str) -> Optional[float]:
    """Parse cursor file content (string) to unix-seconds float or None."""
    content = content.strip()
    if not content:
        return None
    try:
        return float(content)
    except ValueError:
        return None


def load_cursor(paths: list) -> Optional[float]:
    """Return first valid cursor float from candidate paths, or None."""
    for path in paths:
        try:
            with open(path) as f:
                result = parse_cursor(f.read())
            if result is not None:
                return result
        except (FileNotFoundError, OSError):
            continue
    return None


def save_cursor(path: str, ts: float, current: Optional[float] = None) -> bool:
    """Write ts to path only if ts > current (monotonic guarantee).

    Returns True when the write actually happened.
    """
    if current is not None and ts <= current:
        return False
    try:
        os.makedirs(os.path.dirname(path), exist_ok=True)
        with open(path, "w") as f:
            f.write(f"{ts:.3f}")
        return True
    except OSError as exc:
        print(f"  ⚠️ 写 cursor 失败: {exc}")
        return False


def refresh_heartbeat(path: str, *, _save_fn=save_cursor) -> None:
    """Touch path mtime without changing its content.

    Falls back to save_cursor on first boot when the file doesn't exist yet.
    """
    try:
        os.utime(path, None)
    except FileNotFoundError:
        _save_fn(path, time.time())


def parse_create_time(ct) -> Optional[float]:
    """Parse a Feishu message create_time value to unix-seconds float or None.

    Feishu returns either:
      - A formatted string "2026-04-20 09:26"  (lark-cli +chat-messages-list)
      - Unix milliseconds "1776591454415"       (standard API)
      - Unix seconds float "1776591454.415"
    """
    ct_str = str(ct).strip()
    if re.match(r"\d{4}-\d{2}-\d{2}", ct_str):
        try:
            return datetime.strptime(ct_str, "%Y-%m-%d %H:%M").timestamp()
        except ValueError:
            return None
    try:
        v = float(ct_str)
        return v / 1000.0 if v > 1e12 else v
    except (ValueError, TypeError):
        return None
