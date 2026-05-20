'''
Pane supervisor for ClaudeTeam agents. Checks if the tmux panes are alive and marks them as "pane_closed" if not.
This is a best-effort supervisor that runs every `watchdog.pane_supervisor_interval_s`
seconds (default 300) in the watchdog. It uses tmux capture to check if the pane is alive
(based on adapter-specific ready/busy/rate-limit markers) and updates the agent status
accordingly. Agents marked "pane_closed" are candidates for respawn by the lifecycle manager.
The supervisor is best-effort and non-critical: it may miss some pane closures or mark some
alive panes as closed, but it shouldn't cause false alive markings or crash. It's a
cleanup utility to handle edge cases like tmux crashes or agents killed outside of the normal
lifecycle, which would otherwise require manual `claudeteam status` checks and `claudeteam down && up` to fix.
The supervisor is careful to skip agents in "待命" or "已退出" status, which don't need respawning.
The supervisor also has a hook for sending alerts (e.g. Feishu cards) on pane closures, but it's currently only used for watchdog cooldown alerts, not regular pane supervision.
'''

from __future__ import annotations
from typing import Callable
from claudeteam.agents import get_adapter
from claudeteam.runtime import config, tmux

_SKIP_STATUS = frozenset({"待命", "已退出"})  # no need to respawn on these

def _pane_alive(target: tmux.Target, adapter: get_adapter, *, has_window: Callable, capture: Callable) -> bool:
  if not has_window(target):
    return False
  text = capture(target, lines=20)
  if any(m in text for m in adapter.ready_markers()):
    return True
  if any(m in text for m in adapter.busy_markers()):
    return True
  if any(m in text for m in adapter.rate_limit_markers()):
    return True
  return False

def sweep(*, has_window: Callable = tmux.has_window, capture: Callable = tmux.capture_pane, get_status: Callable | None = None, upsert_status: Callable | None = None, Load_team: Callable = config.load_team, session_name: Callable = config.session_name, adapter_for: Callable = get_adapter, log: Callable = print) -> int:
  if get_status is None or upsert_status is None:
    from claudeteam.store.local_facts import get_status as _gs, upsert_status as _us
    get_status = get_status or _gs
    upsert_status = upsert_status or _us
  team = Load_team()
  session = session_name()
  flipped = 0
  for agent, cfg in team.get("agents", {}).items():
    if cfg.get("lazy"):
      continue
    cli = cfg.get("cli", "claude-code")
    try:
      adapter = adapter_for(cli)
    except KeyError:
      continue
    snap = get_status(agent) or {}
    if snap.get("status") in _SKIP_STATUS:
      continue
    target = tmux.Target(session, agent)
    if _pane_alive(target, adapter, has_window=has_window, capture=capture):
      continue
    log(f"  🧹 pane_supervisor: marking {agent} as pane_closed")
    upsert_status(agent, status="pane_closed")
    flipped += 1
  log(f"  🧹 pane_supervisor: swept {len(team.get('agents', {}))} agents, flipped {flipped} to pane_closed")
  return flipped
