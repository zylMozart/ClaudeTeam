#!/usr/bin/env python3
"""Deterministic idle suspend scanner for ClaudeTeam agents."""
from __future__ import annotations

import argparse
import dataclasses
import json
import os
import subprocess
import sys
import time
from pathlib import Path
from typing import Callable, Iterable

PROJECT_ROOT = Path(__file__).resolve().parents[1]
SRC_DIR = PROJECT_ROOT / "src"
if str(SRC_DIR) not in sys.path:
    sys.path.insert(0, str(SRC_DIR))

from claudeteam.runtime.agent_state import AgentState, classify
from claudeteam.runtime.tmux_utils import capture_pane, detect_unsubmitted_input_text
from claudeteam.storage.local_facts import get_status, list_messages

DEFAULT_IDLE_MIN = 15
DEFAULT_NEVER_SUSPEND = {"manager"}
SUSPENDABLE_CODES = {"idle"}
BLOCKING_STATUS = {"进行中", "阻塞"}


@dataclasses.dataclass
class ScanDecision:
    agent: str
    action: str
    reason: str
    state_code: str
    live_cli: bool
    idle_age_s: float | None = None
    status_table_state: str | None = None
    suspended: bool = False
    suspend_returncode: int | None = None


def load_team(team_file: Path | None = None) -> tuple[list[str], str]:
    path = team_file or Path(os.environ.get("CLAUDETEAM_TEAM_FILE") or PROJECT_ROOT / "team.json")
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return [], "ClaudeTeam"
    return list((data.get("agents") or {}).keys()), data.get("session", "ClaudeTeam")


def idle_threshold_s() -> float:
    raw = os.environ.get("CLAUDETEAM_SUSPEND_IDLE_MIN", str(DEFAULT_IDLE_MIN))
    try:
        return max(0.0, float(raw)) * 60
    except ValueError:
        return DEFAULT_IDLE_MIN * 60


def load_never_suspend(project_root: Path = PROJECT_ROOT) -> set[str]:
    agents = set(DEFAULT_NEVER_SUSPEND)
    raw = os.environ.get("CLAUDETEAM_NEVER_SUSPEND", "")
    agents.update(a.strip() for a in raw.split(",") if a.strip())
    overrides = project_root / "agents" / "supervisor" / "workspace" / "overrides.json"
    try:
        data = json.loads(overrides.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return agents
    never = data.get("never_suspend", [])
    if isinstance(never, list):
        agents.update(str(a) for a in never if str(a).strip())
    return agents


def pane_activity_age_s(session: str, agent: str, now: float | None = None) -> float | None:
    try:
        r = subprocess.run(
            ["tmux", "display-message", "-t", f"{session}:{agent}", "-p", "#{pane_activity}"],
            capture_output=True,
            text=True,
            timeout=3,
        )
    except Exception:
        return None
    if r.returncode != 0 or not (r.stdout or "").strip():
        return None
    try:
        return max(0.0, (time.time() if now is None else now) - float(r.stdout.strip()))
    except ValueError:
        return None


def queue_pending(agent: str) -> bool:
    state_dir = os.environ.get("CLAUDETEAM_STATE_DIR", "").strip()
    pending_dir = Path(os.environ.get("CLAUDETEAM_PENDING_DIR", "").strip()) if os.environ.get("CLAUDETEAM_PENDING_DIR", "").strip() else None
    if pending_dir is None and state_dir:
        pending_dir = Path(state_dir) / "queue" / "pending_msgs"
    if pending_dir is None:
        pending_dir = PROJECT_ROOT / "workspace" / "shared" / ".pending_msgs"
    path = pending_dir / f"{agent}.json"
    try:
        return bool(json.loads(path.read_text(encoding="utf-8")))
    except (OSError, json.JSONDecodeError):
        return False


def inbox_unread(agent: str) -> bool:
    try:
        return bool(list_messages(agent, unread_only=True))
    except Exception:
        return False


def status_table_state(agent: str) -> str | None:
    try:
        row = get_status(agent) or {}
        return row.get("status") or None
    except Exception:
        return None


def has_unsubmitted_input(session: str, agent: str) -> bool:
    try:
        return bool(detect_unsubmitted_input_text(capture_pane(session, agent, lines=20)))
    except Exception:
        return False


def append_event(event: dict, project_root: Path = PROJECT_ROOT) -> None:
    path = project_root / "workspace" / "shared" / "lifecycle_events.jsonl"
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8") as f:
        f.write(json.dumps(event, ensure_ascii=False, sort_keys=True) + "\n")


def suspend_agent(agent: str) -> subprocess.CompletedProcess:
    lifecycle = PROJECT_ROOT / "scripts" / "lib" / "agent_lifecycle.sh"
    return subprocess.run(
        ["bash", str(lifecycle), "suspend", agent],
        capture_output=True,
        text=True,
        timeout=60,
    )


def decide_agent(
    agent: str,
    session: str,
    *,
    state: AgentState,
    idle_age_s: float | None,
    idle_threshold: float,
    never_suspend: set[str],
    has_queue: bool,
    has_unread: bool,
    status_state: str | None,
    residual_input: bool,
) -> ScanDecision:
    base = dict(
        agent=agent,
        state_code=state.code,
        live_cli=state.live_cli,
        idle_age_s=idle_age_s,
        status_table_state=status_state,
    )
    if agent in never_suspend:
        return ScanDecision(action="keep", reason="never_suspend", **base)
    if not state.live_cli:
        return ScanDecision(action="keep", reason="cli_not_live", **base)
    if state.code not in SUSPENDABLE_CODES:
        return ScanDecision(action="keep", reason=f"state_{state.code}", **base)
    if idle_age_s is None:
        return ScanDecision(action="keep", reason="pane_activity_unknown", **base)
    if idle_age_s < idle_threshold:
        return ScanDecision(action="keep", reason="idle_threshold_not_met", **base)
    if residual_input:
        return ScanDecision(action="keep", reason="unsubmitted_input", **base)
    if has_queue:
        return ScanDecision(action="keep", reason="queue_pending", **base)
    if has_unread:
        return ScanDecision(action="keep", reason="inbox_unread", **base)
    if status_state in BLOCKING_STATUS:
        return ScanDecision(action="keep", reason="status_table_busy", **base)
    return ScanDecision(action="suspend", reason="idle_threshold_met", **base)


def scan_once(
    *,
    agents: Iterable[str] | None = None,
    session: str | None = None,
    idle_threshold: float | None = None,
    never_suspend: set[str] | None = None,
    classify_fn: Callable[[str, str], AgentState] = classify,
    pane_age_fn: Callable[[str, str], float | None] = pane_activity_age_s,
    queue_pending_fn: Callable[[str], bool] = queue_pending,
    inbox_unread_fn: Callable[[str], bool] = inbox_unread,
    status_fn: Callable[[str], str | None] = status_table_state,
    residual_fn: Callable[[str, str], bool] = has_unsubmitted_input,
    suspend_fn: Callable[[str], subprocess.CompletedProcess] = suspend_agent,
    event_fn: Callable[[dict], None] = append_event,
    dry_run: bool = False,
) -> list[ScanDecision]:
    if agents is None or session is None:
        loaded_agents, loaded_session = load_team()
        agents = loaded_agents if agents is None else agents
        session = loaded_session if session is None else session
    threshold = idle_threshold_s() if idle_threshold is None else idle_threshold
    whitelist = load_never_suspend() if never_suspend is None else never_suspend
    decisions: list[ScanDecision] = []
    now = time.time()

    for agent in agents:
        state = classify_fn(agent, session)
        status_state = status_fn(agent)
        decision = decide_agent(
            agent,
            session,
            state=state,
            idle_age_s=pane_age_fn(session, agent),
            idle_threshold=threshold,
            never_suspend=whitelist,
            has_queue=queue_pending_fn(agent),
            has_unread=inbox_unread_fn(agent),
            status_state=status_state,
            residual_input=residual_fn(session, agent),
        )
        if decision.action == "suspend" and not dry_run:
            result = suspend_fn(agent)
            decision.suspend_returncode = result.returncode
            decision.suspended = result.returncode == 0
            if not decision.suspended:
                decision.action = "keep"
                decision.reason = "suspend_failed"
        event_fn({
            "ts": now,
            "event": "supervisor_scan",
            "agent": decision.agent,
            "action": decision.action,
            "reason": decision.reason,
            "state_code": decision.state_code,
            "live_cli": decision.live_cli,
            "idle_age_s": decision.idle_age_s,
            "idle_threshold_s": threshold,
            "status_table_state": decision.status_table_state,
            "suspended": decision.suspended,
            "suspend_returncode": decision.suspend_returncode,
            "dry_run": dry_run,
        })
        decisions.append(decision)
    return decisions


def print_decisions(decisions: list[ScanDecision]) -> None:
    for d in decisions:
        age = "?" if d.idle_age_s is None else f"{int(d.idle_age_s)}s"
        print(f"{d.agent}: {d.action} reason={d.reason} state={d.state_code} idle={age}")


def run_daemon(interval_s: float, **kwargs) -> None:
    while True:
        print_decisions(scan_once(**kwargs))
        time.sleep(interval_s)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Deterministic ClaudeTeam suspend scanner")
    sub = parser.add_subparsers(dest="cmd", required=True)
    scan_p = sub.add_parser("scan", help="run one scan")
    scan_p.add_argument("--dry-run", action="store_true")
    daemon_p = sub.add_parser("daemon", help="run scan loop")
    daemon_p.add_argument("--interval", type=float, default=float(os.environ.get("CLAUDETEAM_SUPERVISOR_SCAN_INTERVAL", "60")))
    daemon_p.add_argument("--dry-run", action="store_true")
    args = parser.parse_args(argv)

    if args.cmd == "scan":
        print_decisions(scan_once(dry_run=args.dry_run))
        return 0
    if args.cmd == "daemon":
        run_daemon(args.interval, dry_run=args.dry_run)
        return 0
    return 2


if __name__ == "__main__":
    raise SystemExit(main())
