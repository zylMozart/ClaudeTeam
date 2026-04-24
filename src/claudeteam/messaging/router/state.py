"""Router mutable state and pure per-event helpers.

RouterState holds all mutable fields that persist across events in one
daemon run. All methods that read files accept injected callables so the
class is testable without disk or subprocess.
"""
from __future__ import annotations

import json
import os
import re
from collections import OrderedDict
from typing import List, Optional


SEEN_IDS_MAX = 10_000


class RouterState:
    """All mutable router state, centralised in one place."""

    def __init__(self) -> None:
        self.bot_open_id: str = ""
        self.chat_id: str = ""
        self.first_event_at: Optional[float] = None
        # OrderedDict as a bounded LRU set: FIFO eviction at SEEN_IDS_MAX.
        # ~3 MB upper bound; prevents unbounded growth in long-running daemons.
        self.seen_ids: OrderedDict = OrderedDict()
        self._team_mtime: float = 0.0
        self._agent_names: List[str] = []

    # ── seen_ids helpers ──────────────────────────────────────────

    def is_seen(self, msg_id: str) -> bool:
        return msg_id in self.seen_ids

    def mark_seen(self, msg_id: str) -> None:
        self.seen_ids[msg_id] = None
        if len(self.seen_ids) > SEEN_IDS_MAX:
            self.seen_ids.popitem(last=False)

    # ── agent list ────────────────────────────────────────────────

    def reload_agents(self, team_file: str) -> List[str]:
        """Hot-reload agent names from team.json when mtime changes."""
        try:
            mt = os.path.getmtime(team_file)
            if mt != self._team_mtime:
                with open(team_file) as f:
                    data = json.load(f)
                self._agent_names = list(data.get("agents", {}).keys())
                self._team_mtime = mt
                print(f"🔄 Agent 列表已刷新: {', '.join(self._agent_names)}")
        except Exception as exc:
            print(f"⚠️ reload_agents 失败: {exc}")
        return self._agent_names

    # ── pure per-message helpers ──────────────────────────────────

    def is_bot_message(self, sender_id: str) -> bool:
        return bool(self.bot_open_id and sender_id == self.bot_open_id)

    def parse_targets(self, text: str, agents: List[str]) -> List[str]:
        """Return agents explicitly @-mentioned in text."""
        return [name for name in agents if f"@{name}" in text]

    def parse_sender(self, text: str, agents: List[str]) -> Optional[str]:
        """Extract the sending agent name from a 【name·...】 header, or None."""
        m = re.search(r"【(\w[\w-]*)[\s·]", text)
        if m:
            name = m.group(1)
            if name in agents:
                return name
        return None
