"""SlashContext — dependency container injected into every slash handler."""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone, timedelta
from pathlib import Path
from typing import Callable, List, Optional

BJ_TZ = timezone(timedelta(hours=8))


def _noop_capture(agent: str) -> str:
    return ""


def _noop_send(session: str, agent: str, msg: str) -> bool:
    return False


def _noop_query_usage(tool: str):
    return []


def _now_bj() -> datetime:
    return datetime.now(BJ_TZ)


@dataclass
class SlashContext:
    """All I/O dependencies for slash command handlers.

    Handlers receive a context instead of calling subprocess / tmux directly,
    making them unit-testable without live infrastructure.
    """
    team_agents: List[str] = field(default_factory=list)
    tmux_session: str = "ClaudeTeam"
    project_root: Path = field(default_factory=lambda: Path("."))

    # I/O callables — swap out in tests
    capture_pane: Callable[[str], str] = field(default=_noop_capture)
    send_to_agent: Callable[[str, str, str], bool] = field(default=_noop_send)
    query_usage: Callable[[str], list] = field(default=_noop_query_usage)
    now_bj: Callable[[], datetime] = field(default=_now_bj)

    @property
    def agent_set(self) -> frozenset:
        return frozenset(self.team_agents)
