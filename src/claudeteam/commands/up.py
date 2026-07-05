"""`claudeteam up` — bring the whole team alive in one shot.

Composes existing primitives:
  1. `start` — tmux session + per-agent windows + CLI spawn (or lazy)
  2. `router` (detached) — long-running event subscriber
  3. `watchdog` (detached) — supervisor that re-spawns router if it dies

Skip steps where the resource is already alive (idempotent restart).
Returns 0 if everything ends up alive, 1 if any required step failed.

Fast-fail guard: each daemon spawn waits up to 3s for its
pid file to appear under `state_dir/`. If no pid file shows up
(daemon `error_exit`'d before pidlock — usually missing chat_id /
no team agents / port collision), `up` reports the boot failure and
returns rc=1 instead of silently saying `✅ team up`. Operator runs
`claudeteam <name>` directly to see the actual error message.
"""
from __future__ import annotations

import time

from claudeteam.commands import start as _start
from claudeteam.runtime import config, tmux, watchdog
from claudeteam.util import error_exit, maybe_print_help


def _ensure_started() -> int:
    session = config.session_name()
    if tmux.has_session(session):
        print(f"⏭  tmux session {session} already running, skipping start")
        return 0
    return _start.main([])


def _ensure_daemon(spec: watchdog.ProcessSpec) -> int:
    if watchdog.is_alive(spec):
        print(f"⏭  {spec.name} already alive, skipping")
        return 0
    if not watchdog.respawn(spec):
        # respawn() already prints the OS error reason; up.py just sets rc=1.
        return error_exit(f"❌ failed to spawn {spec.name}")
    # Wait up to 3s for the daemon to write its pid file. The pidlock
    # acquire happens immediately after early config validation in the
    # daemon's main(), so 3s is generous for a healthy spawn. If the
    # daemon fast-failed (e.g. missing chat_id, no team agents, port
    # collision), no pid file appears — treat that as failure rather
    # than silently saying "team up". Otherwise a missing chat_id gives
    # "⚠️ launched but no pid file yet" + rc=0, masking the boot
    # failure from `claudeteam up`'s exit code.
    for _ in range(30):
        if spec.pid_file.exists():
            print(f"🚀 {spec.name} launched (pid {spec.pid_file.read_text().strip()})")
            return 0
        time.sleep(0.1)
    return error_exit(
        f"❌ {spec.name} launched but didn't write a pid file in 3s — "
        f"likely fast-failed at startup; check `claudeteam {spec.name}` "
        f"directly to see the error")


def _summon_roster() -> None:
    """Kick the manager to run a team roll-call: it announces in the group,
    summons each worker, and every worker reports back. This is the autonomous
    self-check — no human, no system-posted cards; the live agent loop
    (manager dispatch → worker replies) is what proves the team actually works.
    Called only on a FRESH `up` (after the router is live so the loop can
    route), and best-effort — a failed kick must not fail `up`."""
    from claudeteam.agents import get_adapter
    from claudeteam.runtime import wake
    if "manager" not in config.agent_names():
        return  # no manager to drive the roll-call
    try:
        adapter = get_adapter(config.agent_cli("manager"))
        target = tmux.Target(config.session_name(), "manager")
        wake.inject_and_confirm(
            target, adapter,
            "【系统】团队已全部上线。请你现在在群里发起一次全员点名："
            "先在群里说明需要全体员工汇报，然后逐一通知每位员工，让他们"
            "各自在群里汇报自己的身份与当前状态，最后由你汇总确认。")
    except Exception:  # noqa: BLE001 — best-effort; `up` already succeeded
        pass


def main(argv: list[str]) -> int:
    if maybe_print_help(argv, "usage: claudeteam up"):
        return 0

    # Preflight: the router can't subscribe / the crew can't report in without a
    # registered bot + group. `up` stays non-interactive (it runs on restarts /
    # watchdog respawns), so point at `feishu connect` rather than dropping into
    # the guided registration here.
    if not config.chat_id():
        return error_exit(
            "❌ 未配置飞书机器人 / 群（chat_id 为空）。\n"
            "   先运行 `claudeteam feishu connect` 引导注册自建应用并自动建群，"
            "再 `claudeteam up`。")

    # A fresh bring-up always clears a /shutdown pause — otherwise the ACP
    # fleet would stay dormant while every pane and daemon looks healthy.
    from claudeteam.runtime import acp_host
    acp_host.resume_all()

    # Fresh bring-up (session didn't exist yet) → the manager runs the roll-call
    # once everything's live. Restarts/idempotent `up` skip it (no re-spam).
    fresh = not tmux.has_session(config.session_name())

    rc = _ensure_started()
    if rc != 0:
        return rc

    for spec in watchdog.all_known_specs():
        rc |= _ensure_daemon(spec)

    if rc == 0:
        print("✅ team up — run `claudeteam health` to verify")
        if fresh:
            _summon_roster()
            print("📣 已让主管在群里发起全员点名（自检：看主管+全员汇报）")
    else:
        print("⚠️  team up with errors — see above; "
              "`claudeteam health` will list which daemons died")
    return rc
