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
from typing import List, Optional, Tuple


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
        # BOSS_MOCK (smoke only): treat bot self-send as a boss message.
        # Double-guard: BOTH env vars must be set so prod can never accidentally enable.
        self._boss_mock = (
            os.environ.get("CLAUDETEAM_BOSS_MOCK") == "1"
            and os.environ.get("CLAUDETEAM_RUNTIME_PROFILE") == "smoke"
        )

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
        # mock-boss profile: bot self-send is treated as boss message, not bot self
        if self._boss_mock:
            return False
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

    def parse_prefix_target(
        self, text: str, agents: List[str]
    ) -> Tuple[Optional[str], str]:
        """Detect per-agent prefix routing in a user message.

        Recognized forms (case-insensitive):
          @worker_cc <body>
          worker_cc:<body>   /   worker_cc：<body>
          worker_cc <body>

        Returns (canonical_agent_name, stripped_body) when a known agent
        prefix is found and a non-empty body follows; otherwise (None, text).
        """
        if not text or not agents:
            return None, text
        # Sort longest first so 'worker_codex' wins over a hypothetical 'worker'.
        agent_alt = "|".join(re.escape(a) for a in sorted(agents, key=len, reverse=True))
        pat = re.compile(
            rf"^\s*@?({agent_alt})(?:\s*[:：]\s*|\s+)(.+)$",
            re.IGNORECASE | re.DOTALL,
        )
        m = pat.match(text)
        if not m:
            return None, text
        canon = next((a for a in agents if a.lower() == m.group(1).lower()), None)
        if not canon:
            return None, text
        body = m.group(2).strip()
        if not body:
            return None, text
        return canon, body
