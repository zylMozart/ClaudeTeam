"""Pure helpers for watchdog daemon pid liveness / PID reuse checks."""
from __future__ import annotations

from typing import Callable


def parse_pid_text(raw: str | None) -> int | None:
    try:
        return int((raw or "").strip())
    except (TypeError, ValueError):
        return None


def is_expected_cmdline(cmdline: str | None, expected_fragment: str = "watchdog.py") -> bool:
    return expected_fragment in (cmdline or "")


def is_live_pid_probe(
    pid: int | None,
    *,
    pid_alive: bool,
    cmdline: str | None,
    expected_fragment: str = "watchdog.py",
) -> bool:
    if pid is None or not pid_alive:
        return False
    return is_expected_cmdline(cmdline, expected_fragment=expected_fragment)


def pid_file_is_live(
    path: str,
    *,
    path_exists: Callable[[str], bool],
    read_text: Callable[[str], str],
    pid_is_alive: Callable[[int], bool],
    read_cmdline: Callable[[int], str],
    expected_fragment: str = "watchdog.py",
) -> bool:
    if not path_exists(path):
        return False

    try:
        pid = parse_pid_text(read_text(path))
        if pid is None:
            return False
        if not pid_is_alive(pid):
            return False
        cmdline = read_cmdline(pid)
    except (OSError, ValueError, TypeError):
        return False

    return is_live_pid_probe(
        pid,
        pid_alive=True,
        cmdline=cmdline,
        expected_fragment=expected_fragment,
    )
