"""`claudeteam restart <agent>` — non-destructive pane rebuild.

THE path for "I changed this agent's model/CLI in claudeteam.toml" or
"its CLI is stuck / OOM'd". Kills the pane and re-provisions it FROM the
current roster config (so a new `model` / `cli` takes effect). It does
NOT archive, does NOT remove from the roster, does NOT mark 已停止.

Deliberately separate from `fire`, which is destructive 裁员 (archive +
delete from roster). Before this command existed, operators restarted a
pane with `fire` + `hire`; under the restored destructive `fire` that
would archive + delete the agent — a foot-gun for the model-switch /
routine-restart / down→up workflows. `restart` is the safe rebuild.

(For role / specialty / identity edits that DON'T need a fresh CLI
process, `claudeteam reidentify <agent>` re-injects identity in place
without a respawn — cheaper than a restart.)

Refuses if the agent isn't in the roster (use `hire`) or the session
isn't running (use `up`).
"""
from __future__ import annotations

from claudeteam.runtime import config, lifecycle, tmux
from claudeteam.util import error_exit, maybe_print_help, usage_error, warn


USAGE = "usage: claudeteam restart <agent>"


def main(argv: list[str]) -> int:
    if maybe_print_help(argv, USAGE):
        return 0
    if len(argv) < 1:
        return usage_error(USAGE)
    agent = argv[0]

    try:
        cfg = config.agent_config(agent)
    except KeyError:
        return error_exit(
            f"❌ unknown agent: {agent} (not in roster; use `claudeteam hire {agent}`)")
    cli = cfg.get("cli", "claude-code")

    session = config.session_name()
    if not tmux.has_session(session):
        return error_exit(
            f"❌ tmux session {session} not running; run `claudeteam up` first")

    target = tmux.Target(session, agent)
    # ACP agent: the CLI lives inside the router's AcpHost, not the pane.
    # Recycle it — a durable `stop` row tears the subprocess down, and
    # dropping session.json forces a FRESH session (new model / cli from
    # the roster takes effect + identity turn re-runs) instead of a
    # session/load resume of the old config.
    if config.agent_runner(agent) == "acp":
        from claudeteam.runtime import acp_host
        if acp_host.recycle(agent):
            print(f"♻️  {agent}: ACP subprocess recycled (fresh session on next message)")
        else:
            warn("⚠️ ACP recycle best-effort failed")
    # Kill the existing pane if present — no archive, no roster change, no
    # 已停止 — then re-create the window so provision starts from a clean
    # shell prompt (same contract provision_pane expects from hire/start).
    if tmux.has_window(target):
        tmux.send_keys(target, "C-c")
        tmux.kill_window(target)
        print(f"♻️  {agent}: killed old pane")
    if not tmux.new_window(target):
        return error_exit(f"❌ failed to re-create window for {agent}")

    outcome = lifecycle.provision_pane(agent, target)
    if outcome == lifecycle.LAZY:
        print(f"✅ restarted (lazy): {agent} ({cli}) → {target}")
        return 0
    if outcome == lifecycle.CONFIG_ERROR:
        return error_exit(
            f"❌ {agent}: bad cli config in claudeteam.toml (see warning above)")
    if outcome == lifecycle.SPAWN_FAILED:
        return error_exit(f"❌ failed to spawn CLI in {agent} pane")
    if outcome == lifecycle.READY_NO_INIT:
        warn(f"⚠️  {agent} CLI didn't show ready marker in time; "
             f"identity init prompt skipped")
    print(f"✅ restarted: {agent} ({cli}) → {target}")
    return 0
