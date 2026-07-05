"""`claudeteam team-shutdown` / `claudeteam team-restart` — the detached
runners behind the `/shutdown` and `/restart` chat slash commands.

They are normal CLI subcommands (not buried in the slash handler) for two
reasons: they can be unit-tested in isolation, and the slash handler can
launch them with a plain detached Popen — a child process that survives
`down` killing the router, which an in-router thread could not.

Each runs the lifecycle primitive(s), then posts a completion card to the
team chat via `teamctl.notify` (the router is gone by then, so the slash
handler that triggered this can't report the outcome itself).

These are also usable directly by an operator (`claudeteam team-restart`);
they are NOT gated by `allow_lifecycle_slash` — that flag guards the CHAT
surface only. A shell operator already has full host access.
"""
from __future__ import annotations

from claudeteam.commands import down as _down, up as _up
from claudeteam.feishu import cards
from claudeteam.runtime import config, teamctl, tmux
from claudeteam.util import maybe_print_help, warn


def _shutdown_agents() -> int:
    """Tear down ONLY the agent tmux session (panes), deliberately leaving
    the router + its feishu `+subscribe` chain + the watchdog ALIVE.

    This is what makes /shutdown recoverable from chat. A full `down` also
    kills the router (and the lark-cli subscription it owns), so afterwards
    nothing is listening and `/restart` can never be received — recovery then
    needs operator shell access. Killing only the tmux
    session drops every agent pane (the actual "team offline" the operator
    wants) while the router stays subscribed and can act on a later `/restart`
    to re-wake the team. The watchdog is left up too, so if the router dies
    while the team is dormant it gets respawned and `/restart` still works.

    Best-effort like `down`: a not-running session is success (0); only a
    kill that actually fails sets rc=1."""
    # ACP agents live inside the router, not the panes — killing the tmux
    # session alone only removes their viewers while they keep consuming
    # queues. Pause the fleet first (sessions stay on disk, so a later
    # /restart resumes context via session/load).
    from claudeteam.runtime import acp_host
    if acp_host.pause_all():
        print("⏸  ACP agents paused (queues held; sessions kept for /restart)")
    else:
        warn("⚠️  failed to pause ACP agents (state dir unwritable?)")

    session = config.session_name()
    if not tmux.has_session(session):
        print(f"⏭  tmux session {session} not running")
        return 0
    if tmux.kill_session(session):
        print(f"🛑 tmux session {session} killed "
              "(router / feishu subscription / watchdog kept alive)")
        return 0
    warn(f"⚠️  failed to kill tmux session {session}")
    return 1


def shutdown_main(argv: list[str]) -> int:
    if maybe_print_help(argv, "usage: claudeteam team-shutdown"):
        return 0
    # NOT `down` — keep router + subscription + watchdog alive so a later
    # `/restart` can be received and re-wake the team without shell access.
    rc = _shutdown_agents()
    if rc == 0:
        teamctl.notify(cards.simple_card(
            "团队控制 · /shutdown",
            f"🛑 团队已下线（session `{config.session_name()}` 的 agent pane 全部关闭）。\n"
            "router + 飞书订阅 + watchdog 仍在线监听——直接 `/restart` 即可重新唤醒，"
            "无需运维登服务器。",
            color="green"))
    else:
        teamctl.notify(cards.simple_card(
            "团队控制 · /shutdown",
            "⚠️ 下线 agent 会话过程有告警（tmux 没干净退出），请查看容器日志。",
            color="red"))
    return rc


def restart_main(argv: list[str]) -> int:
    if maybe_print_help(argv, "usage: claudeteam team-restart"):
        return 0
    # Phase 1 — robust teardown. `down` already escalates SIGTERM→SIGKILL
    # and reaps the subscribe process group, so it IS the straggler clean:
    # dead/stale pidfiles, orphan tmux session+windows, leftover npx/node
    # from a previous router. If it can't get everything dead, abort —
    # don't stack a fresh team on top of a half-dead one.
    rc = _down.main([])
    if rc != 0:
        teamctl.notify(cards.simple_card(
            "团队控制 · /restart",
            "⚠️ 下线阶段有残留没杀干净，已**中止重启**（不在半死团队上叠新团队）。"
            "请查看容器日志后手动处理，再 `/restart` 或运维 `up`。",
            color="red"))
        return rc
    # Phase 2 — bring it back. up is idempotent and waits on each daemon's
    # pidfile, so a fast-fail (missing chat_id, no agents) surfaces as rc=1.
    rc = _up.main([])
    if rc == 0:
        teamctl.notify(cards.simple_card(
            "团队控制 · /restart",
            f"♻️ 团队已重启完成（session `{config.session_name()}`）。"
            "`/health` 可核验各守护进程。",
            color="green"))
    else:
        teamctl.notify(cards.simple_card(
            "团队控制 · /restart",
            "⚠️ 重启的 up 阶段有错误（某守护进程没起来），请查看容器日志 / `/health`。",
            color="red"))
    return rc
