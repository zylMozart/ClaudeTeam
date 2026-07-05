"""`claudeteam fire <agent>` — 裁员 (destructive removal).

`fire` REMOVES an agent for good. It:
  1. Ctrl-C's + kills the agent's tmux pane.
  2. Marks status 已停止 — an authoritative retirement tombstone the four
     pane-spawning paths consult (`local_facts.is_retired`) so an in-flight
     message can't silently revive it.
  3. Archives the agent's workspace dir (`<state_dir>/agents/<name>/` →
     `.../agents/_archived/<name>_<date>/` with a `_termination.md` record
     + a `_roster.json` stash) — see `runtime/archive.py`.
  4. Deletes the agent from the team roster (claudeteam.toml
     `[team.agents.<name>]`) so `start`/`up` never re-provision it.

⚠️ fire is NOT a restart. To rebuild a pane (changed model in
claudeteam.toml, stuck CLI, re-read config) use **`claudeteam restart
<agent>`** — non-destructive: keeps the roster + workspace. To bring a
fired agent back, **`claudeteam hire <agent>`** restores from the latest
archive. Using fire as a generic restart would archive + delete the agent.

Refuses to fire 'manager' (kill the tmux session yourself if you mean it).
"""
from __future__ import annotations

from claudeteam.runtime import archive, config, paths, tmux
from claudeteam.store import local_facts
from claudeteam.util import env_str, error_exit, maybe_print_help, usage_error


USAGE = "usage: claudeteam fire <agent>"


def main(argv: list[str]) -> int:
    if maybe_print_help(argv, USAGE):
        return 0
    if len(argv) < 1:
        return usage_error(USAGE)
    agent = argv[0]

    if agent == "manager":
        return error_exit(
            "❌ refusing to fire manager (kill the tmux session yourself if you mean it)")

    # Capture the roster cfg + the VERBATIM toml block BEFORE removal so the
    # archive can restore it byte-for-byte on a future rehire (the block text
    # is immune to the dict-reserialization edge cases — multi-line values,
    # alignment, in-block comments — that would otherwise corrupt the roster).
    # Empty cfg if the agent isn't in the roster (already fired / hand-removed)
    # — we still kill any pane + archive any workspace.
    try:
        cfg = config.agent_config(agent)
    except KeyError:
        cfg = {}
    block_text = config.extract_agent_block(agent)

    session = config.session_name()
    target = tmux.Target(session, agent)
    if tmux.has_window(target):
        # Ctrl-C to interrupt whatever's running, then kill the window.
        tmux.send_keys(target, "C-c")
        tmux.kill_window(target)
        print(f"✅ fired: {agent} (pane killed)")
    else:
        print(f"⚠️  {agent} has no live pane in session {session}")

    local_facts.upsert_status(agent, local_facts.RETIRED_STATUS, "fired")

    # ACP agent: the CLI subprocess lives in the router's AcpHost, not the
    # pane we just killed. Recycle tears it down promptly (the worker's own
    # retired-check would catch it too, just slower) and drops the saved
    # session so it can't survive into a rehire.
    if config.agent_runner(agent) == "acp":
        from claudeteam.runtime import acp_host
        if acp_host.recycle(agent):
            print(f"  ⏹  {agent}: ACP subprocess stop queued")

    # Archive the workspace — but only if there's actually something to
    # preserve (a roster cfg to stash, or a workspace dir). Re-firing an
    # already-fired agent (no cfg, no workspace) would otherwise write a
    # fresh, stash-less archive dir that, being newer, shadows the original
    # good archive and breaks rehire. Best-effort: a failed archive must
    # not strand the roster, so removal below still runs.
    if cfg or paths.agent_dir(agent).exists():
        try:
            dst = archive.archive_agent(agent, cfg, block_text=block_text,
                                        executor=env_str("CLAUDETEAM_ACTOR"))
            print(f"🗄  archived workspace → {dst}")
        except OSError as e:
            print(f"  ⚠️ workspace archive failed for {agent}: {e}")
    else:
        print(f"  ⏭  nothing to archive for {agent} (already fired?)")

    # Wipe the per-agent secret file (resolved API key / OAuth token, 0600) so a
    # fired agent's credential doesn't linger in the state dir.
    try:
        (paths.state_dir() / "spawn-env" / f"{agent}.sh").unlink(missing_ok=True)
    except OSError:
        pass

    # Remove from the roster so start/up never revive it (root fix).
    ok, msg = config.remove_agent(agent)
    print(f"🗑  {msg}" if ok else f"  ⚠️ roster removal skipped: {msg}")
    print(f"   ({agent} fired. `claudeteam hire {agent}` restores from archive; "
          f"`claudeteam restart` is the non-destructive rebuild.)")
    return 0
