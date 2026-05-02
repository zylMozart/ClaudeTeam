"""`claudeteam health` — one-shot deployment-state check.

Reports, with green/red glyphs, the things that have to be true for
this team to actually deliver messages:

  - state_dir resolved (and from where: env vs default)
  - team.json + runtime_config.json present, with chat_id set
  - tmux session alive
  - per-agent: pane exists? CLI shows a ready marker?
  - router/watchdog: pid file present? process alive? cmdline matches?
  - router cursor: present? last-seen message id

Exit code: 0 if everything green, 1 if any red. Yellow (warning) does
not fail the check.
"""
from __future__ import annotations

import json
import os

from claudeteam.agents import adapter_for_agent
from claudeteam.feishu import catchup
from claudeteam.runtime import config, paths, tmux, watchdog
from claudeteam.store import local_facts
from claudeteam.util import ago_ms, error_exit, help_requested


_OK = "✅"
_BAD = "❌"
_WARN = "⚠️ "


def _check_state_dir(out: list[str]) -> None:
    sd = paths.state_dir()
    src = "env" if os.environ.get("CLAUDETEAM_STATE_DIR") else "default (~/.claudeteam)"
    out.append(f"  state_dir: {sd}  ({src})")


def _check_team(out: list[str]) -> int:
    bad = 0
    tf = config.team_file()
    if not tf.exists():
        out.append(f"  {_BAD} team.json missing at {tf}")
        return 1
    try:
        team = config.load_team()
    except json.JSONDecodeError as e:
        out.append(f"  {_BAD} team.json parse error: {e}")
        return 1
    agents = team.get("agents", {})
    out.append(f"  {_OK} team.json: {len(agents)} agent(s) ({tf})")
    if not agents:
        out.append(f"  {_WARN} team.json has no agents")
    return bad


def _check_runtime_config(out: list[str]) -> int:
    bad = 0
    rc = config.runtime_config_file()
    if not rc.exists():
        out.append(f"  {_BAD} runtime_config.json missing at {rc}")
        return 1
    cfg = config.load_runtime_config()
    chat = cfg.get("chat_id", "")
    if not chat:
        out.append(f"  {_BAD} runtime_config.json has empty chat_id")
        bad = 1
    else:
        out.append(f"  {_OK} chat_id: {chat}")
    profile = config.lark_profile()
    if profile:
        out.append(f"  {_OK} lark_profile: {profile}")
    else:
        out.append(f"  {_WARN} lark_profile blank — bot identity required for sends")
    return bad


def _check_session(out: list[str], session: str) -> bool:
    if tmux.has_session(session):
        out.append(f"  {_OK} tmux session: {session}")
        return True
    out.append(f"  {_BAD} tmux session {session} not running (run `claudeteam start`)")
    return False


def _check_agents(out: list[str], session: str, agents: list[str], session_alive: bool) -> int:
    bad = 0
    heartbeats = local_facts.all_heartbeats()
    for agent in agents:
        target = tmux.Target(session, agent)
        line = f"    {agent}"
        hb = heartbeats.get(agent)
        hb_suffix = f"  ♥ {ago_ms(hb)}" if hb else "  ♥ never"
        if not session_alive:
            out.append(f"  {_WARN} {line}: session down, skip{hb_suffix}")
            continue
        if not tmux.has_window(target):
            out.append(f"  {_BAD} {line}: no tmux window{hb_suffix}")
            bad = 1
            continue
        try:
            adapter = adapter_for_agent(agent)
            text = tmux.capture_pane(target, lines=80)
            if any(m in text for m in adapter.ready_markers()):
                out.append(f"  {_OK} {line}: pane ready ({config.agent_cli(agent)}){hb_suffix}")
            else:
                out.append(f"  {_WARN} {line}: pane up but no CLI ready marker (lazy or starting?){hb_suffix}")
        except Exception as e:
            out.append(f"  {_WARN} {line}: probe failed — {e}")
    return bad


def _check_daemon(out: list[str], spec: watchdog.ProcessSpec) -> int:
    if not spec.pid_file.exists():
        out.append(f"  {_WARN} {spec.name}: no pid file (not running?)")
        return 0
    if watchdog.is_alive(spec):
        out.append(f"  {_OK} {spec.name}: alive ({spec.pid_file.read_text().strip()})")
        return 0
    out.append(f"  {_BAD} {spec.name}: pid file present but process dead")
    return 1


def _check_cursor(out: list[str]) -> None:
    cur = catchup.read_cursor()
    if cur:
        mid = cur.get("message_id", "?")
        ct = cur.get("create_time", "?")
        out.append(f"  {_OK} router cursor: {mid} (create_time={ct})")
    else:
        out.append(f"  {_WARN} router cursor: empty (first run, or never advanced)")


def main(argv: list[str]) -> int:
    if help_requested(argv):
        print("usage: claudeteam health")
        return 0

    out: list[str] = []
    bad = 0

    out.append("paths:")
    _check_state_dir(out)
    out.append("")

    out.append("config:")
    bad += _check_team(out)
    bad += _check_runtime_config(out)
    out.append("")

    try:
        team = config.load_team()
        session = team.get("session", "ClaudeTeam")
        agents = sorted(team.get("agents", {}))
    except Exception:
        team, session, agents = {}, "ClaudeTeam", []

    out.append("tmux:")
    session_alive = _check_session(out, session)
    bad += 0 if session_alive else 1
    if agents:
        bad += _check_agents(out, session, agents, session_alive)
    out.append("")

    out.append("daemons:")
    for spec in watchdog.all_known_specs():
        bad += _check_daemon(out, spec)
    out.append("")

    out.append("router state:")
    _check_cursor(out)

    print("\n".join(out))
    if bad:
        return error_exit(f"\n{_BAD} {bad} red check(s) — see above")
    print(f"\n{_OK} all green")
    return 0
