"""Runtime wrapper: single-arg dispatch(text) with live context.

Builds a SlashContext from the live environment (team.json, tmux, usage tools)
and delegates to the pure dispatch(text, ctx) function.

Public API:
    dispatch(text) -> (matched: bool, reply)
"""
import json
import os
import subprocess
import sys
import time
from datetime import datetime, timezone, timedelta
from pathlib import Path

_SRC_DIR = str(Path(__file__).resolve().parents[4])
if _SRC_DIR not in sys.path:
    sys.path.insert(0, _SRC_DIR)

from claudeteam.commands.slash.context import SlashContext
from claudeteam.commands.slash.dispatch import dispatch as _src_dispatch
from claudeteam.runtime.health import collect_health as _collect_health_impl
from claudeteam.runtime.config import PROJECT_ROOT

BJ_TZ = timezone(timedelta(hours=8))


def _load_team():
    tf = (os.environ.get("CLAUDETEAM_TEAM_FILE", "").strip()
          or str(Path(PROJECT_ROOT) / "team.json"))
    try:
        d = json.loads(Path(tf).read_text())
        return list(d.get("agents", {}).keys()), d.get("session", "ClaudeTeam")
    except Exception:
        return ["manager"], "ClaudeTeam"


def _capture_pane(agent: str) -> str:
    agents, session = _load_team()
    r = subprocess.run(
        ["tmux", "capture-pane", "-t", f"{session}:{agent}", "-p", "-S", "-50"],
        capture_output=True, text=True, timeout=5)
    return r.stdout if r.returncode == 0 else ""


def _send_to_agent(session: str, agent: str, msg: str) -> bool:
    target = f"{session}:{agent}"
    try:
        from claudeteam.runtime.tmux_utils import inject_when_idle
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
    except FileNotFoundError:
        if "claude" in tool:
            snapshot = Path(PROJECT_ROOT) / "scripts" / "usage_snapshot.py"
            if snapshot.exists():
                try:
                    r = subprocess.run(["python3", str(snapshot)],
                                       capture_output=True, text=True, timeout=30)
                    if r.returncode == 0 and r.stdout.strip():
                        return r.stdout.splitlines()
                except Exception:
                    pass
    except Exception:
        pass
    return []


def build_context() -> SlashContext:
    agents, session = _load_team()
    agent_set = frozenset(agents)
    return SlashContext(
        team_agents=agents,
        tmux_session=session,
        project_root=Path(PROJECT_ROOT),
        capture_pane=_capture_pane,
        send_to_agent=_send_to_agent,
        query_usage=_query_usage,
        now_bj=lambda: datetime.now(BJ_TZ),
        collect_health=lambda: _collect_health_impl(agent_set, session),
    )


def dispatch(text: str):
    """Route text to a slash handler. Returns (matched: bool, reply)."""
    return _src_dispatch(text, build_context())
