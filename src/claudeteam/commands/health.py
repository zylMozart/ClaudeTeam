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
import shutil

from claudeteam.agents import adapter_for_agent
from claudeteam.feishu import catchup
from claudeteam.runtime import config, paths, tmux, watchdog
from claudeteam.store import local_facts
from claudeteam.util import ago_ms, env_str, error_exit, help_requested


_OK = "✅"
_BAD = "❌"
_WARN = "⚠️ "
_INFO = "ℹ️ "


def _check_state_dir(out: list[str]) -> None:
    sd = paths.state_dir()
    src = "env" if env_str("CLAUDETEAM_STATE_DIR") else "default (~/.claudeteam)"
    out.append(f"  state_dir: {sd}  ({src})")


def _check_team(out: list[str]) -> int:
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
    return 0


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
            elif config.agent_config(agent).get("lazy"):
                out.append(f"  {_OK} {line}: lazy pane (CLI starts on first message){hb_suffix}")
            else:
                out.append(f"  {_WARN} {line}: pane up but CLI not ready yet — wait a few seconds or check the pane{hb_suffix}")
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


def _check_binaries(out: list[str], agents: list[str]) -> int:
    """For each unique CLI process_name (claude/codex/kimi/...), verify the
    binary is on PATH. Missing binaries don't crash claudeteam, but every
    pane spawn will fail to launch its CLI."""
    bad = 0
    seen: dict[str, list[str]] = {}
    for agent in agents:
        try:
            name = adapter_for_agent(agent).process_name()
        except Exception:
            continue
        seen.setdefault(name, []).append(agent)
    for binary, used_by in sorted(seen.items()):
        path = shutil.which(binary)
        if path:
            out.append(f"  {_OK} {binary}: {path}  (used by {', '.join(used_by)})")
        else:
            out.append(f"  {_BAD} {binary}: not on PATH  (used by {', '.join(used_by)})")
            bad = 1
    return bad


def _check_proxy_env(out: list[str]) -> None:
    """If HTTPS_PROXY/HTTP_PROXY is set without LARK_CLI_NO_PROXY=1, lark-cli
    requests transit through the proxy — usually fatal on host networks.
    Warning only (not fatal): user may genuinely want the proxy."""
    proxy = env_str("HTTPS_PROXY") or env_str("HTTP_PROXY")
    if not proxy:
        return
    if env_str("LARK_CLI_NO_PROXY").lower() in {"1", "true", "yes", "on"}:
        out.append(f"  {_OK} HTTPS_PROXY set ({proxy}) but LARK_CLI_NO_PROXY=1 — wrapper will strip")
    else:
        out.append(
            f"  {_WARN} HTTPS_PROXY={proxy} set without LARK_CLI_NO_PROXY=1; "
            "lark-cli requests may fail. `export LARK_CLI_NO_PROXY=1` to strip.")


def _check_cursor(out: list[str]) -> None:
    cur = catchup.read_cursor()
    if cur:
        mid = cur.get("message_id", "?")
        ct = cur.get("create_time", "?")
        out.append(f"  {_OK} router cursor: {mid} (create_time={ct})")
    else:
        # No cursor is normal until the first inbound event lands; advancement
        # only happens for events coming OFF the wire, not for self-originated
        # `say` calls. Mark informational, not a warning.
        out.append(f"  {_INFO} router cursor: empty (advances on first inbound event)")


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
        session, agents = "ClaudeTeam", []

    if agents:
        out.append("binaries:")
        bad += _check_binaries(out, agents)
        out.append("")

    out.append("env:")
    _check_proxy_env(out)
    out.append("")

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
    warns = sum(1 for line in out if _WARN.strip() in line)
    if warns:
        print(f"\n{_WARN} no errors, {warns} warning(s) — see above")
    else:
        print(f"\n{_OK} all green")
    return 0
