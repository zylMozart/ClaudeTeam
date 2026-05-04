"""`claudeteam router`

Long-running event subscriber: spawns `lark-cli event +subscribe`
(direct binary preferred, npx fallback — see `feishu/lark.resolve_cli_prefix`,
R86 + R139) and feeds each NDJSON line into the routing loop
(`feishu/subscribe.process_lines`).

Boot order:
  1. Validate chat_id + agents (fast-fail BEFORE pidlock so up.py can
     detect "no pid written" and surface the boot error — R62).
  2. Acquire `state_dir/router.pid` via pidlock so two routers can't
     fight.
  3. Replay `pending_lines(chat_id)` to backfill anything received while
     the daemon was down (catchup-on-restart cursor).
  4. Spawn the subscribe subprocess in its own session (so SIGTERMing
     the daemon kills the entire npx → node → lark-cli tree via
     killpg).
  5. Spawn a daemon thread that polls the subscribe child's exit code
     every ~20s and self-SIGTERMs when it dies (R52: lark-cli sometimes
     dies silently while npm-exec parent keeps stdout open, blocking
     readline forever).
  6. Drive `process_lines` over the subscribe stdout iterator.

Stops on:
  - Ctrl-C → SIGINT
  - SIGTERM → registered handler reaps the subscribe group, releases
    pidlock, exits 0.
  - subscribe child dies → watchdog thread SIGTERMs us; same cleanup.

Writes the daemon's pid to `state_dir/router.pid` so watchdog can
supervise via `runtime.watchdog.is_alive`. Watchdog separately reaps
orphan `+subscribe` processes left by a SIGKILL'd predecessor before
respawning (R65 `reap_orphans`).
"""
from __future__ import annotations

import os
import signal
import subprocess
import sys
import threading
import time

from claudeteam.feishu import catchup, lark
from claudeteam.feishu.deliver import apply as _deliver_apply
from claudeteam.feishu.subscribe import process_lines
from claudeteam.runtime import config, paths, pidlock, wake
from claudeteam.util import error_exit, maybe_print_help, warn


def _build_subscribe_cmd(profile: str, *,
                         resolve_prefix=lark.resolve_cli_prefix) -> list[str]:
    """Build the lark-cli `event +subscribe` argv.

    Round-139: prefix comes from `lark.resolve_cli_prefix` (direct
    binary first, `npx @larksuite/cli` fallback) instead of hardcoding
    npx. The hardcode predated R86's direct-binary work and was an
    invisible perf miss — every router restart paid the npx
    package-lookup overhead even though the equivalent `call()` path
    had skipped it for months. Tests inject `resolve_prefix=` so the
    argv shape is deterministic regardless of what's installed locally.

    Note on --force: previously included to bypass the single-instance
    lock from a possibly-zombie previous daemon. lark-cli 1.0.21+ docs
    that flag explicitly: "UNSAFE: server randomly splits events across
    connections, each instance only receives a subset". Removing it
    means events flow to one connection (ours); the lock file at
    ~/.lark-cli/locks/subscribe_<app_id>.lock is fcntl-advisory, so it
    auto-releases on process exit. claudeteam's own pidlock + the
    watchdog respawn keep us at one daemon at a time, so the
    single-instance lock is harmless.
    """
    return [
        *resolve_prefix(),
        *(["--profile", profile] if profile else []),
        "event", "+subscribe",
        "--event-types", "im.message.receive_v1",
        "--compact", "--quiet",
        "--as", "bot",
    ]


def _apply_with_wake(decision):
    """Production deliver wrapper: lazy-wake panes before injecting."""
    return _deliver_apply(decision, wake_fn=wake.wake_if_dormant)


def _terminate_subscribe_group(proc: subprocess.Popen) -> None:
    """Kill the entire subscribe process group (npx + node + lark-cli).

    Round 7 D2: router's plain proc.terminate() only signaled npx; the
    lark-cli grandchild lived on as an orphan after each up/down cycle.
    Putting the subprocess in its own session (start_new_session=True at
    Popen time) means we can take the whole group out with one killpg.
    """
    if proc.poll() is not None:
        return
    try:
        os.killpg(os.getpgid(proc.pid), signal.SIGTERM)
    except (ProcessLookupError, PermissionError):
        return
    try:
        proc.wait(timeout=3)
    except subprocess.TimeoutExpired:
        try:
            os.killpg(os.getpgid(proc.pid), signal.SIGKILL)
        except (ProcessLookupError, PermissionError):
            pass


def _on_progress(decision, stats):
    """After each handled event, advance the catchup cursor."""
    catchup.record_decision(decision)


# How often the subscribe-watchdog thread checks whether the lark-cli
# child is still alive. Short enough to detect a silent death in <30s,
# long enough not to busy-loop.
_SUBSCRIBE_WATCHDOG_PERIOD_S = 20.0


def _watch_subscribe_health(proc: subprocess.Popen, stop_event: threading.Event) -> None:
    """Background thread: kill the daemon if the subscribe child dies.

    `lark-cli event +subscribe` periodically dies silently — Round B Smoke
    + round-52 smoke both saw the lark-cli grandchild vanish while npm-exec
    parent stayed running. With npm-exec still holding stdout open, the
    main thread's `process_lines(proc.stdout, ...)` would block forever
    on readline, never noticing.

    This thread polls proc.poll() every ~20s. When the npm parent exits
    (which usually follows lark-cli's death within a few seconds), it
    terminates the entire subscribe group + raises SIGTERM at the daemon
    so the SIGTERM handler runs cleanup. Watchdog respawns from there.
    """
    while not stop_event.wait(_SUBSCRIBE_WATCHDOG_PERIOD_S):
        if proc.poll() is not None:
            print(f"  ⚠️ subscribe child exited (rc={proc.returncode}); router will exit so watchdog can respawn")
            # Send SIGTERM to ourselves; the registered handler will
            # reap the subscribe group and exit cleanly.
            os.kill(os.getpid(), signal.SIGTERM)
            return


def main(argv: list[str]) -> int:
    if maybe_print_help(argv, "usage: claudeteam router"):
        return 0

    chat = config.chat_id()
    if not chat:
        return error_exit("❌ chat_id not set in runtime_config.json")

    agents = config.agent_names()
    if not agents:
        return error_exit("❌ team.json has no agents")

    pid_file = paths.router_pid_file()
    if not pidlock.acquire(pid_file, name="router"):
        return 1

    profile = config.lark_profile()
    cmd = _build_subscribe_cmd(profile)
    print(f"🚀 router subscribing on chat {chat} (profile={profile or '<default>'})")

    try:
        # Two precautions on the subscribe child:
        # - env=lark.subprocess_env() strips HTTPS_PROXY under LARK_CLI_NO_PROXY=1
        #   (round 6 D-class bug — lark-cli long-poll dies behind a proxy).
        # - start_new_session=True puts the npx → node → lark-cli chain in its
        #   own process group so SIGTERMing the router can kill the whole tree
        #   in one killpg call (round 7 D2 — orphaned grandchildren).
        proc = subprocess.Popen(
            cmd,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            text=True,
            bufsize=1,  # line-buffered
            env=lark.subprocess_env(),
            start_new_session=True,
        )
    except FileNotFoundError:
        pidlock.release(pid_file)
        return error_exit("❌ npx / lark-cli not found in PATH")

    # Now that proc exists, install a SIGTERM handler that reaps the
    # subscribe group before exiting. (Plain sys.exit propagates SystemExit
    # past the except blocks, never running proc.terminate.)
    def _on_sigterm(*_):
        _terminate_subscribe_group(proc)
        sys.exit(0)
    signal.signal(signal.SIGTERM, _on_sigterm)

    # Spawn the subscribe-health watchdog thread. It exits the daemon
    # cleanly if lark-cli dies under us — without it, process_lines would
    # block forever on stdout that npm-exec parent keeps open after the
    # lark-cli grandchild vanishes.
    stop_watchdog = threading.Event()
    threading.Thread(
        target=_watch_subscribe_health, args=(proc, stop_watchdog),
        daemon=True,
    ).start()

    try:
        if proc.stdout is None:
            return error_exit("❌ lark-cli started without stdout pipe")

        loop_kwargs = dict(
            team_agents=agents,
            chat_id=chat,
            default_target="manager",
            apply_fn=_apply_with_wake,
            on_progress=_on_progress,
        )

        # Catchup: replay anything newer than the cursor before going live
        try:
            pending = catchup.pending_lines(chat, profile=profile)
        except Exception as e:
            warn(f"⚠️  catchup fetch failed: {e}")
            pending = []
        if pending:
            print(f"📥 catching up {len(pending)} missed message(s)")
            process_lines(iter(pending), **loop_kwargs)

        stats = process_lines(proc.stdout, **loop_kwargs)
        print(f"router exited: handled={stats.handled} dropped={stats.dropped}")
        return 0 if proc.wait() == 0 else 1
    except KeyboardInterrupt:
        print("router stopped (Ctrl-C)")
        return 0
    finally:
        # Reap the subscribe tree on EVERY exit path so we don't leak a
        # node + lark-cli pair per up/down cycle.
        stop_watchdog.set()
        _terminate_subscribe_group(proc)
        pidlock.release(pid_file)
