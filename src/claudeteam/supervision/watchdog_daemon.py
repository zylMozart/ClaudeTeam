"""Pure helpers for watchdog daemon pid liveness / PID reuse checks."""
from __future__ import annotations

from typing import Callable
from claudeteam.supervision.pid_helpers import (
    parse_pid_text as parse_pid_text,
    is_expected_cmdline as _is_expected_cmdline,
    is_live_pid_probe as _is_live_pid_probe,
    pid_file_is_live as _pid_file_is_live,
)

_FRAG = "watchdog.py"


def is_expected_cmdline(cmdline: str | None, expected_fragment: str = _FRAG) -> bool:
    return _is_expected_cmdline(cmdline, expected_fragment)


def is_live_pid_probe(
    pid: int | None,
    *,
    pid_alive: bool,
    cmdline: str | None,
    expected_fragment: str = _FRAG,
) -> bool:
    return _is_live_pid_probe(pid, pid_alive=pid_alive, cmdline=cmdline,
                               expected_fragment=expected_fragment)


def pid_file_is_live(
    path: str,
    *,
    path_exists: Callable[[str], bool],
    read_text: Callable[[str], str],
    pid_is_alive: Callable[[int], bool],
    read_cmdline: Callable[[int], str],
    expected_fragment: str = _FRAG,
) -> bool:
    return _pid_file_is_live(path, path_exists=path_exists, read_text=read_text,
                              pid_is_alive=pid_is_alive, read_cmdline=read_cmdline,
                              expected_fragment=expected_fragment)
