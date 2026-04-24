#!/usr/bin/env python3
"""Thin compat shell — delegates to src/claudeteam/commands/slash.

All callers using `slash_commands.dispatch(text)` continue to work unchanged.
The shell builds a live SlashContext from the runtime environment and calls
the src dispatch function.
"""
import json
import os
import subprocess
import sys
import time
from datetime import datetime, timezone, timedelta
from pathlib import Path

_SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
_SRC_DIR = os.path.join(os.path.dirname(_SCRIPT_DIR), "src")
for _p in (_SCRIPT_DIR, _SRC_DIR):
    if _p not in sys.path:
        sys.path.insert(0, _p)

from claudeteam.commands.slash import SlashContext
from claudeteam.commands.slash.dispatch import dispatch as _src_dispatch
from claudeteam.commands.slash.health import collect_health as _collect_health_impl

_PROJECT_ROOT = Path(_SCRIPT_DIR).parent
BJ_TZ = timezone(timedelta(hours=8))


# ── team loader ───────────────────────────────────────────────────────────────

def _load_team():
    tf = (os.environ.get("CLAUDETEAM_TEAM_FILE", "").strip()
          or str(_PROJECT_ROOT / "team.json"))
    try:
        d = json.loads(Path(tf).read_text())
        return list(d.get("agents", {}).keys()), d.get("session", "ClaudeTeam")
    except Exception:
        return ["manager"], "ClaudeTeam"


AGENT_WINDOWS, _SESSION = _load_team()
AGENT_SET = set(AGENT_WINDOWS)


# ── live I/O callables ────────────────────────────────────────────────────────

def _capture_pane(agent: str) -> str:
    r = subprocess.run(
        ["tmux", "capture-pane", "-t", f"{_SESSION}:{agent}", "-p", "-S", "-50"],
        capture_output=True, text=True, timeout=5)
    return r.stdout if r.returncode == 0 else ""


def _send_to_agent(session: str, agent: str, msg: str) -> bool:
    target = f"{session}:{agent}"
    try:
        from tmux_utils import inject_when_idle
        return inject_when_idle(session, agent, msg, wait_secs=5, force_after_wait=True)
    except Exception:
        pass
    r = subprocess.run(["tmux", "send-keys", "-l", "-t", target, msg],
                       capture_output=True)
    if r.returncode != 0:
        return False
    time.sleep(0.2)
    subprocess.run(["tmux", "send-keys", "-t", target, "Enter"], capture_output=True)
    return True


def _query_usage(tool: str) -> list:
    try:
        r = subprocess.run([tool], capture_output=True, text=True, timeout=15)
        if r.returncode == 0 and r.stdout.strip():
            return r.stdout.splitlines()
    except Exception:
        pass
    return []


def _build_ctx() -> SlashContext:
    agents, session = _load_team()
    agent_set = frozenset(agents)
    return SlashContext(
        team_agents=agents,
        tmux_session=session,
        project_root=_PROJECT_ROOT,
        capture_pane=_capture_pane,
        send_to_agent=_send_to_agent,
        query_usage=_query_usage,
        now_bj=lambda: datetime.now(BJ_TZ),
        collect_health=lambda: _collect_health_impl(agent_set, session),
    )


# ── public API (backward-compat signature) ────────────────────────────────────

def dispatch(text: str):
    """Route text to a slash handler. Returns (matched: bool, reply)."""
    return _src_dispatch(text, _build_ctx())
