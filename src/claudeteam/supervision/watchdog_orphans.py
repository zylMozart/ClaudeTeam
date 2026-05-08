"""Pure helpers for watchdog orphan-victim selection."""
from __future__ import annotations

from typing import Iterable, Mapping


def parse_ppid_from_status_text(status_text: str | None) -> int | None:
    text = status_text or ""
    for line in text.splitlines():
        if line.startswith("PPid:"):
            parts = line.split()
            if len(parts) < 2:
                return None
            try:
                return int(parts[1])
            except (TypeError, ValueError):
                return None
    return None


def select_router_tree_victims(
    *,
    tree_pids: Iterable[int],
    router_pid: int | None,
    my_pid: int,
    is_lark_subscribe: Mapping[int, bool],
) -> list[int]:
    victims: list[int] = []
    for pid in tree_pids:
        if pid == my_pid or pid == router_pid:
            continue
        if is_lark_subscribe.get(pid, False):
            victims.append(pid)
    return victims


def select_orphan_victims(
    *,
    candidate_pids: Iterable[int],
    my_pid: int,
    is_lark_subscribe: Mapping[int, bool],
    ppid_by_pid: Mapping[int, int | None],
) -> list[int]:
    victims: list[int] = []
    for pid in candidate_pids:
        if pid == my_pid:
            continue
        if not is_lark_subscribe.get(pid, False):
            continue
        if ppid_by_pid.get(pid) == 1:
            victims.append(pid)
    return victims
