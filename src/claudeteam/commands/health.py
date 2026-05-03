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
from dataclasses import dataclass, field

from claudeteam.agents import adapter_for_agent
from claudeteam.feishu import catchup
from claudeteam.runtime import config, paths, tmux, watchdog
from claudeteam.store import local_facts
from claudeteam.util import ago_ms, env_str, error_exit, help_requested, pop_bool_flag


_OK = "✅"
_BAD = "❌"
_WARN = "⚠️ "
_INFO = "ℹ️ "


@dataclass
class HealthReport:
    """Accumulator handed to every `_check_*`. Emission and counting
    happen in one place so we don't string-search the formatted output
    later to figure out how many warnings we logged.
    """
    lines: list[str] = field(default_factory=list)
    bad: int = 0
    warn: int = 0

    def ok(self, msg: str) -> None:
        self.lines.append(f"  {_OK} {msg}")

    def fail(self, msg: str) -> None:
        self.lines.append(f"  {_BAD} {msg}")
        self.bad += 1

    def yellow(self, msg: str) -> None:
        self.lines.append(f"  {_WARN}{msg}")
        self.warn += 1

    def info(self, msg: str) -> None:
        self.lines.append(f"  {_INFO}{msg}")

    def note(self, msg: str) -> None:
        """Indented plain line (no glyph)."""
        self.lines.append(f"  {msg}")

    def section(self, title: str) -> None:
        """Unindented section header."""
        self.lines.append(title)

    def blank(self) -> None:
        self.lines.append("")


def _check_state_dir(rep: HealthReport) -> None:
    src = "env" if env_str("CLAUDETEAM_STATE_DIR") else "default (~/.claudeteam)"
    rep.note(f"state_dir: {paths.state_dir()}  ({src})")


def _check_team(rep: HealthReport) -> None:
    tf = config.team_file()
    if not tf.exists():
        rep.fail(f"team.json missing at {tf}")
        return
    try:
        team = config.load_team()
    except json.JSONDecodeError as e:
        rep.fail(f"team.json parse error: {e}")
        return
    agents = team.get("agents", {})
    rep.ok(f"team.json: {len(agents)} agent(s) ({tf})")
    if not agents:
        rep.yellow("team.json has no agents")


def _check_runtime_config(rep: HealthReport) -> None:
    rc = config.runtime_config_file()
    if not rc.exists():
        rep.fail(f"runtime_config.json missing at {rc}")
        return
    cfg = config.load_runtime_config()
    if chat := cfg.get("chat_id", ""):
        rep.ok(f"chat_id: {chat}")
    else:
        rep.fail("runtime_config.json has empty chat_id")
    if profile := config.lark_profile():
        rep.ok(f"lark_profile: {profile}")
    else:
        rep.yellow("lark_profile blank — bot identity required for sends")


def _check_session(rep: HealthReport, session: str) -> bool:
    if tmux.has_session(session):
        rep.ok(f"tmux session: {session}")
        return True
    rep.fail(f"tmux session {session} not running (run `claudeteam start`)")
    return False


def _check_agents(rep: HealthReport, session: str, agents: list[str],
                  session_alive: bool) -> None:
    heartbeats = local_facts.all_heartbeats()
    for agent in agents:
        target = tmux.Target(session, agent)
        hb = heartbeats.get(agent)
        hb_suffix = f"  ♥ {ago_ms(hb)}" if hb else "  ♥ never"
        if not session_alive:
            rep.yellow(f"  {agent}: session down, skip{hb_suffix}")
            continue
        if not tmux.has_window(target):
            rep.fail(f"  {agent}: no tmux window{hb_suffix}")
            continue
        try:
            adapter = adapter_for_agent(agent)
            text = tmux.capture_pane(target, lines=80)
            if any(m in text for m in adapter.ready_markers()):
                rep.ok(f"  {agent}: pane ready ({config.agent_cli(agent)}){hb_suffix}")
            elif config.agent_config(agent).get("lazy"):
                rep.ok(f"  {agent}: lazy pane (CLI starts on first message){hb_suffix}")
            else:
                rep.yellow(f"  {agent}: pane up but CLI not ready yet — wait a few seconds or check the pane{hb_suffix}")
        except Exception as e:
            rep.yellow(f"  {agent}: probe failed — {e}")


def _check_daemon(rep: HealthReport, spec: watchdog.ProcessSpec) -> None:
    if not spec.pid_file.exists():
        rep.yellow(f"{spec.name}: no pid file (not running?)")
        return
    if watchdog.is_alive(spec):
        rep.ok(f"{spec.name}: alive ({spec.pid_file.read_text().strip()})")
        return
    rep.fail(f"{spec.name}: pid file present but process dead")


def _check_binaries(rep: HealthReport, agents: list[str]) -> None:
    """For each unique CLI process_name (claude/codex/kimi/...), verify the
    binary is on PATH. Missing binaries don't crash claudeteam, but every
    pane spawn will fail to launch its CLI."""
    seen: dict[str, list[str]] = {}
    for agent in agents:
        try:
            name = adapter_for_agent(agent).process_name()
        except Exception:
            continue
        seen.setdefault(name, []).append(agent)
    for binary, used_by in sorted(seen.items()):
        users = ", ".join(used_by)
        path = shutil.which(binary)
        if path:
            rep.ok(f"{binary}: {path}  (used by {users})")
        else:
            rep.fail(f"{binary}: not on PATH  (used by {users})")


def _check_proxy_env(rep: HealthReport) -> None:
    """If HTTPS_PROXY/HTTP_PROXY is set without LARK_CLI_NO_PROXY=1, lark-cli
    requests transit through the proxy — usually fatal on host networks.
    Warning only (not fatal): user may genuinely want the proxy."""
    proxy = env_str("HTTPS_PROXY") or env_str("HTTP_PROXY")
    if not proxy:
        return
    if env_str("LARK_CLI_NO_PROXY").lower() in {"1", "true", "yes", "on"}:
        rep.info(f"HTTPS_PROXY set ({proxy}) but LARK_CLI_NO_PROXY=1 — wrapper will strip")
    else:
        rep.yellow(
            f"HTTPS_PROXY={proxy} set without LARK_CLI_NO_PROXY=1; "
            "lark-cli requests may fail. `export LARK_CLI_NO_PROXY=1` to strip.")


def _check_cursor(rep: HealthReport) -> None:
    cur = catchup.read_cursor()
    if cur:
        rep.ok(f"router cursor: {cur.get('message_id', '?')} (create_time={cur.get('create_time', '?')})")
    else:
        # Empty cursor is normal until the first inbound event lands;
        # advancement only happens for events coming OFF the wire, not
        # for self-originated `say` calls. Informational, not warning.
        rep.info("router cursor: empty (advances on first inbound event)")


def _build_report() -> HealthReport:
    """Run every check and return the populated HealthReport. Pure
    enumeration — main() picks the renderer (text or JSON) and the
    exit code based on rep.bad."""
    rep = HealthReport()

    rep.section("paths:")
    _check_state_dir(rep)
    rep.blank()

    rep.section("config:")
    _check_team(rep)
    _check_runtime_config(rep)
    rep.blank()

    try:
        team = config.load_team()
        session = team.get("session", "ClaudeTeam")
        agents = sorted(team.get("agents", {}))
    except Exception:
        session, agents = "ClaudeTeam", []

    if agents:
        rep.section("binaries:")
        _check_binaries(rep, agents)
        rep.blank()

    rep.section("env:")
    _check_proxy_env(rep)
    rep.blank()

    rep.section("tmux:")
    session_alive = _check_session(rep, session)
    if agents:
        _check_agents(rep, session, agents, session_alive)
    rep.blank()

    rep.section("daemons:")
    for spec in watchdog.all_known_specs():
        _check_daemon(rep, spec)
    rep.blank()

    rep.section("router state:")
    _check_cursor(rep)

    return rep


def _emit_text(rep: HealthReport) -> None:
    """Default renderer: the formatted lines + a summary footer."""
    print("\n".join(rep.lines))
    if rep.bad:
        print(f"\n{_BAD} {rep.bad} red check(s) — see above")
    elif rep.warn:
        print(f"\n{_WARN}no errors, {rep.warn} warning(s) — see above")
    else:
        print(f"\n{_OK} all green")


def _emit_json(rep: HealthReport) -> None:
    """Machine-readable shape:
        {"ok": bool, "bad": int, "warn": int, "lines": [str, ...]}
    Smoke conductors / CI can branch on `ok` and inspect `lines` for
    the rendered glyphs (which still appear in `lines`, just packaged)."""
    print(json.dumps({
        "ok": rep.bad == 0,
        "bad": rep.bad,
        "warn": rep.warn,
        "lines": list(rep.lines),
    }, ensure_ascii=False, indent=2))


def main(argv: list[str]) -> int:
    rest = list(argv)
    if help_requested(rest):
        print("usage: claudeteam health [--json]")
        return 0
    as_json = pop_bool_flag(rest, "--json")
    if rest:
        return error_exit(f"❌ unexpected args: {rest}\nusage: claudeteam health [--json]")

    rep = _build_report()
    if as_json:
        _emit_json(rep)
    else:
        _emit_text(rep)
    return 1 if rep.bad else 0
