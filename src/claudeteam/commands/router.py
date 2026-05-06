"""`claudeteam router`

Long-running event subscriber: spawns `lark-cli event +subscribe`
(direct binary preferred, npx fallback — see
`feishu/lark.resolve_cli_prefix`) and feeds each NDJSON line into
the routing loop (`feishu/subscribe.process_lines`).

Boot order:
  1. Validate chat_id + agents (fast-fail BEFORE pidlock so up.py
     can detect "no pid written" and surface the boot error).
  2. Acquire `state_dir/router.pid` via pidlock so two routers
     can't fight.
  3. Replay `pending_lines(chat_id)` to backfill anything received
     while the daemon was down (catchup-on-restart cursor).
  4. Spawn the subscribe subprocess in its own session (so
     SIGTERMing the daemon kills the entire npx → node → lark-cli
     tree via killpg).
  5. Spawn a daemon thread that polls the subscribe child's exit
     code every ~20s and self-SIGTERMs when it dies (lark-cli
     occasionally exits silently while npm-exec parent keeps
     stdout open, blocking readline forever).
  6. Drive `process_lines` over the subscribe stdout iterator.

Stops on:
  - Ctrl-C → SIGINT
  - SIGTERM → handler reaps subscribe group, releases pidlock, exit 0
  - subscribe child dies → watchdog thread SIGTERMs us; same cleanup.

Writes pid to `state_dir/router.pid` so `runtime.watchdog.is_alive`
can supervise. Watchdog separately reaps orphan `+subscribe`
processes left by a SIGKILL'd predecessor before respawning.
"""
from __future__ import annotations

import os
import signal
import subprocess
import sys
import threading
import time
from typing import Callable

from claudeteam.feishu import catchup, lark
from claudeteam.feishu.deliver import apply as _deliver_apply
from claudeteam.feishu.subscribe import process_lines
from claudeteam.runtime import config, paths, pidlock, wake
from claudeteam.util import error_exit, maybe_print_help, warn


def _build_subscribe_cmd(profile: str, *,
                         resolve_prefix=lark.resolve_cli_prefix) -> list[str]:
    """Build the lark-cli `event +subscribe` argv.

    Prefix comes from `lark.resolve_cli_prefix` (direct binary first,
    `npx @larksuite/cli` fallback). Tests inject `resolve_prefix=`
    so the argv shape is deterministic regardless of what's
    installed locally.

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


def _build_agent_adapters(agents_dict: dict) -> dict:
    """Resolve every team-known agent to its CliAdapter once.

    Pre-building this map keeps `_inject_to_pane`'s per-target adapter
    lookup disk-read-free for cached agents. Adapters whose `cli`
    value is bogus get skipped (no entry); the apply call falls back
    to the config-driven lookup which surfaces the KeyError as a
    per-agent warning instead of a build-time abort.
    """
    from claudeteam.agents import get_adapter
    adapters: dict = {}
    for name, cfg in agents_dict.items():
        cli = cfg.get("cli", "claude-code")
        try:
            adapters[name] = get_adapter(cli)
        except KeyError:
            continue
    return adapters


def _make_apply_with_wake(*, session: str, chat_id: str, profile: str,
                          team_agents: list[str], agent_adapters: dict,
                          lazy_agents: frozenset):
    """Build the per-event deliver wrapper with hot-path config pre-bound.

    chat_id / lark_profile / session are deployment-stable; binding
    them in a closure here saves 2-4 disk reads per inbound message
    compared to letting `deliver.apply` re-resolve via `config.<getter>()`
    each time. The pre-built `agent → CliAdapter` map plays the same
    role for `_inject_to_pane` — unknown agents (not in the cached
    map) fall back to a config-driven lookup so a typo surfaces as a
    per-agent warning instead of dropping the whole event.

    Operator edits to `chat_id` need a `claudeteam down + up` to take
    effect (subscribe is bound to the startup chat_id, pidlock
    prevents a parallel daemon). Per-agent fields like `lazy` /
    `card_color` / `specialty` ARE live-readable through other code
    paths (slash handlers via `_live_agents()`, identity via
    `claudeteam reidentify`).
    """
    def lookup_adapter(agent: str):
        cached = agent_adapters.get(agent)
        if cached is not None:
            return cached
        from claudeteam.agents import adapter_for_agent
        return adapter_for_agent(agent)

    def _apply_with_wake(decision):
        return _deliver_apply(decision, wake_fn=wake.wake_if_dormant,
                              session=session, chat_id=chat_id,
                              profile=profile, team_agents=team_agents,
                              lazy_agents=lazy_agents,
                              adapter_for_agent=lookup_adapter)
    return _apply_with_wake


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


_SEEN_MAX_LINES = 5000   # truncate router.seen file when it grows past this


def _load_seen_msg_ids() -> set[str]:
    """Load persisted dedup set from disk, truncating to the most recent
    SEEN_MAX_LINES entries to bound the file. Returns empty set if the
    file is missing or unreadable — best-effort, never fails the daemon.
    """
    path = paths.router_seen_file()
    try:
        if not path.exists():
            return set()
        with path.open("r", encoding="utf-8") as f:
            ids = [line.strip() for line in f if line.strip()]
    except OSError:
        return set()
    if len(ids) > _SEEN_MAX_LINES:
        # Truncate file in place so it doesn't grow unbounded.
        try:
            kept = ids[-_SEEN_MAX_LINES:]
            path.write_text("\n".join(kept) + "\n", encoding="utf-8")
            ids = kept
        except OSError:
            pass
    return set(ids)


def _make_on_progress(last_event_at: list[float]) -> Callable:
    """Build the on_progress callback bound to a mutable timestamp slot.

    Every successfully handled (non-DROP) event:
    - refreshes `last_event_at[0]` so the subscribe-watchdog thread can
      detect "lark-cli subprocess alive but events stopped flowing" —
      the silent-failure mode that bouncing the router fixes.
    - appends the message_id to `state/router.seen` so the dedup set
      survives across process restarts. Without this, router self-
      restarts (driven by stale-detect or watchdog) re-apply messages
      that catchup re-fetches because seen_msg_ids was an in-memory
      set (host_smoke 2026-05-06: /tmux manager card forwarded into
      manager inbox every ~3.5min on every restart cycle).
    """
    def _on_progress(decision, stats):
        catchup.record_decision(decision)
        last_event_at[0] = time.monotonic()
        msg_id = getattr(decision, "msg_id", "")
        if msg_id:
            try:
                seen_path = paths.router_seen_file()
                seen_path.parent.mkdir(parents=True, exist_ok=True)
                with seen_path.open("a", encoding="utf-8") as f:
                    f.write(msg_id + "\n")
            except OSError:
                pass  # best-effort; in-memory set still dedups in this run
    return _on_progress


# How often the subscribe-watchdog thread checks whether the lark-cli
# child is still alive. Short enough to detect a silent death in <30s,
# long enough not to busy-loop.
_SUBSCRIBE_WATCHDOG_PERIOD_S = 20.0


def _stale_event_threshold_s() -> float:
    """Max seconds router will tolerate with no inbound event before
    self-SIGTERM'ing for a watchdog respawn.

    Resolved via runtime.tunables — priority env > claudeteam.toml > default.
    Legacy `CLAUDETEAM_ROUTER_STALE_S` env (without `_EVENT_THRESHOLD`) is
    still honored as a backwards-compat alias since it shipped first.

    Default 180s — observed lark WebSocket silent-stall happens within
    minutes of router boot in 2026-05-06 host_smoke; 1200s default was
    too lax (test caught manager not seeing user message for 7+ minutes).
    """
    from claudeteam.runtime import tunables
    # Legacy env-var alias (shipped before the tunables framework).
    legacy = os.environ.get("CLAUDETEAM_ROUTER_STALE_S", "").strip()
    if legacy:
        try:
            v = float(legacy)
            if v > 0:
                return v
        except ValueError:
            pass
    return float(tunables.tunable("router.stale_event_threshold_s", 180.0))


def _watch_subscribe_health(proc: subprocess.Popen, stop_event: threading.Event,
                            last_event_at: list[float]) -> None:
    """Background thread: kill the daemon if the subscribe child dies OR
    stops delivering events.

    Two failure modes covered:

    (a) `lark-cli event +subscribe` exits silently — the lark-cli
        grandchild can vanish while npm-exec parent stays running.
        With npm-exec still holding stdout open, the main thread's
        `process_lines(proc.stdout, ...)` would block forever on
        readline, never noticing.

    (b) `lark-cli` subprocess stays alive but the WebSocket silently
        stops delivering events.
        proc.poll() is None, the npm tree looks healthy in `ps`, but
        no inbound events reach process_lines for hours. Detected by
        comparing `last_event_at[0]` to wall-clock; threshold from
        `CLAUDETEAM_ROUTER_STALE_S` env or 1200s default.

    Both modes terminate via SIGTERM-to-self so the registered handler
    reaps the subscribe group cleanly. Watchdog respawns from there.
    """
    threshold = _stale_event_threshold_s()
    while not stop_event.wait(_SUBSCRIBE_WATCHDOG_PERIOD_S):
        if proc.poll() is not None:
            print(f"  ⚠️ subscribe child exited (rc={proc.returncode}); router will exit so watchdog can respawn")
            os.kill(os.getpid(), signal.SIGTERM)
            return
        idle = time.monotonic() - last_event_at[0]
        if idle > threshold:
            print(f"  ⚠️ no events for {idle:.0f}s (threshold {threshold:.0f}s); subscribe likely silently stalled, exiting for respawn")
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
    # lark-cli grandchild vanishes. Also self-terminates if events stop
    # flowing for too long (silent-subscribe-stall mode).
    stop_watchdog = threading.Event()
    last_event_at = [time.monotonic()]
    threading.Thread(
        target=_watch_subscribe_health,
        args=(proc, stop_watchdog, last_event_at),
        daemon=True,
    ).start()

    try:
        if proc.stdout is None:
            return error_exit("❌ lark-cli started without stdout pipe")

        # Bind deployment-stable config values into apply_fn at daemon
        # startup so deliver.apply doesn't re-resolve them on every
        # inbound event (saves 1-4 disk reads per message). The
        # agent→adapter map plays the same role for the inject path.
        # `lazy_agents` is still pre-computed and threaded into
        # SlashContext for back-compat, but slash handlers now use
        # `_live_agents()` themselves so config edits are live.
        team_data = config.load_team()
        agents_dict = team_data.get("agents", {})
        apply_fn = _make_apply_with_wake(
            session=team_data.get("session", "ClaudeTeam"),
            chat_id=chat,
            profile=profile,
            team_agents=agents,
            agent_adapters=_build_agent_adapters(agents_dict),
            lazy_agents=frozenset(name for name, cfg in agents_dict.items()
                                  if cfg.get("lazy")),
        )

        # Persisted dedup set — survives daemon restarts so catchup
        # replay after stale-detect / watchdog respawn doesn't re-apply
        # already-handled messages (host_smoke 2026-05-06 caught it).
        seen = _load_seen_msg_ids()
        loop_kwargs = dict(
            team_agents=agents,
            chat_id=chat,
            default_target="manager",
            apply_fn=apply_fn,
            on_progress=_make_on_progress(last_event_at),
            seen_msg_ids=seen,
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
