"""`claudeteam router`

Long-running event subscriber: spawns the `@larksuite/channel` sidecar
(`node scripts/feishu_channel/sidecar.js run`, the official WebSocket →
NDJSON ingress) and feeds each NDJSON line into the routing loop
(`feishu/subscribe.process_lines`).

Boot order:
  1. Validate chat_id + agents (fast-fail BEFORE pidlock so up.py
     can detect "no pid written" and surface the boot error).
  2. Acquire `state_dir/router.pid` via pidlock so two routers
     can't fight.
  3. Replay `pending_lines(chat_id)` to backfill anything received
     while the daemon was down (catchup-on-restart cursor).
  4. Spawn the subscribe subprocess in its own session (so
     SIGTERMing the daemon kills the entire node sidecar
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

import collections
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
from claudeteam.runtime import config, paths, pidlock, tunables, wake
from claudeteam.util import error_exit, maybe_print_help, warn


def _build_subscribe_cmd(profile: str = "", *,
                         sidecar=lark.sidecar_path) -> list[str]:
    """Build the Feishu event-ingress argv: the `@larksuite/channel` sidecar's
    `run` mode (`node scripts/feishu_channel/sidecar.js run`).

    The sidecar opens the official long-connection WebSocket and emits each
    inbound message as one NDJSON line in the lark-cli `--compact` flat shape
    that `feishu.subscribe.process_lines` already parses — so the whole
    routing loop downstream is unchanged. App creds reach it via
    `lark.subprocess_env()` (the same env the Popen uses), not the argv.

    Tests inject `sidecar=` so the argv is deterministic. `profile` is unused
    now (the sidecar binds to the resolved app creds, not a lark-cli profile)
    but kept in the signature so the daemon call site stays as-is.

    Replaces the former `lark-cli event +subscribe` argv: that path silently
    dropped its WebSocket on macOS and split events across connections under
    the old `--force`; the SDK long-connection is the supported ingress.
    """
    return ["node", str(sidecar()), "run"]


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
    """Kill the entire subscribe process tree (npx + node + lark-cli).

    A plain proc.terminate() only signals npx; the lark-cli grandchild
    then lives on as an orphan after each up/down cycle. The child was
    spawned with `util.detached_popen_kwargs()` so the whole tree goes
    in one call (killpg on POSIX, taskkill /T on Windows)."""
    from claudeteam.util import terminate_process_group
    terminate_process_group(proc)


def _load_seen_msg_ids() -> set[str]:
    """Load persisted dedup set from disk, truncating to
    `router.seen_max_lines` (claudeteam.toml; default 5000) to bound the
    file. Returns empty set if missing or unreadable — best-effort,
    never fails the daemon."""
    path = paths.router_seen_file()
    try:
        if not path.exists():
            return set()
        with path.open("r", encoding="utf-8") as f:
            ids = [line.strip() for line in f if line.strip()]
    except OSError:
        return set()
    seen_max = int(tunables.tunable("router.seen_max_lines", 5000))
    if len(ids) > seen_max:
        # Truncate file in place so it doesn't grow unbounded.
        try:
            kept = ids[-seen_max:]
            path.write_text("\n".join(kept) + "\n", encoding="utf-8")
            ids = kept
        except OSError:
            pass
    return set(ids)


def _make_on_progress(last_event_at: list[float], events_seen: list[int]) -> Callable:
    """Build the on_progress callback bound to a mutable timestamp slot.

    Every successfully handled (non-DROP) event:
    - refreshes `last_event_at[0]` so the subscribe-watchdog thread can
      detect "lark-cli subprocess alive but events stopped flowing" —
      the silent-failure mode that bouncing the router fixes.
    - appends the message_id to `state/router.seen` so the dedup set
      survives across process restarts. Without this, router self-
      restarts (driven by stale-detect or watchdog) re-apply messages
      that catchup re-fetches because seen_msg_ids was an in-memory
      set (e.g. a /tmux manager card forwarded into the manager inbox
      every ~3.5min on every restart cycle).
    """
    def _on_progress(decision, stats):
        catchup.record_decision(decision)
        last_event_at[0] = time.monotonic()
        # Count only genuine handled events (NOT every line — keepalive/
        # heartbeat lines bump last_event_at via _bump_subscribe_alive but
        # aren't "inbound events"). Lets the health thread tell "idle since
        # start, never saw traffic" apart from "was flowing, then stalled".
        events_seen[0] += 1
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


def _platform_default_stale_event_threshold_s() -> float:
    """Default stale-event threshold split by platform — root cause
    of the previous 180/600 churn was platform-specific WebSocket
    behaviour, not a single-knob tuning problem.

    macOS (Darwin) → 120s. lark-cli 1.0.23 WebSocket subscribe silently
    drops on macOS without reconnecting (the subscribe child stays
    alive but stops delivering events; only self-SIGTERM + watchdog
    respawn + catchup recovers). A tighter
    threshold lets recovery happen in ~2 min instead of ~10. Quiet-chat
    overhead is acceptable on a dev laptop.

    Linux (and everything else) → 600s. WebSocket is stable here; quiet
    chats shouldn't churn through respawns. History on this platform:
    1200s → too lax (manager not seeing a user msg for 7+ min); 180s →
    too tight (a genuinely quiet chat respawned every ~3 min, churning
    router.log into a wall of "no events for 180s; respawning"). 600s is the
    calibrated middle.
    """
    import platform
    return 120.0 if platform.system() == "Darwin" else 600.0


def _stale_event_threshold_s() -> float:
    """Max seconds router will tolerate with no inbound event before
    self-SIGTERM'ing for a watchdog respawn.

    Resolved via runtime.tunables — priority env > claudeteam.toml >
    platform-aware default (see `_platform_default_stale_event_threshold_s`).
    Legacy `CLAUDETEAM_ROUTER_STALE_S` env (without `_EVENT_THRESHOLD`) is
    still honored as a backwards-compat alias since it shipped first.
    """
    # Legacy env-var alias (shipped before the tunables framework).
    legacy = os.environ.get("CLAUDETEAM_ROUTER_STALE_S", "").strip()
    if legacy:
        try:
            v = float(legacy)
            if v > 0:
                return v
        except ValueError:
            pass
    return float(tunables.tunable(
        "router.stale_event_threshold_s",
        _platform_default_stale_event_threshold_s()))


def _subscribe_rotate_reason(idle: float, threshold: float, events_seen: int) -> str:
    """The router log line printed right before self-SIGTERM for respawn.

    Pure so it's unit-testable without the kill/loop machinery. The whole
    point of #5: the *idle* case (no inbound yet this session) is the NORMAL
    macOS state — the live WebSocket goes quiet, the respawn + catchup-on-
    restart is the designed recovery, NOT a fault. Wording it "silently
    stalled" every ~120s made first deploys look broken. So:

      - events_seen == 0  → calm ℹ️: rotating subscribe, catchup will refetch.
      - events_seen  > 0  → ⚠️: events WERE flowing then stopped (more notable,
                            esp. on Linux where the WS is supposed to be stable).
    """
    if events_seen == 0:
        return (f"  ℹ️ no live events for {idle:.0f}s — rotating subscribe "
                f"(none inbound yet this session; on macOS the WebSocket often "
                f"goes quiet, catchup refetches on restart)")
    return (f"  ⚠️ live events stopped after {idle:.0f}s idle "
            f"(threshold {threshold:.0f}s) — rotating subscribe for respawn")


# Signatures in the sidecar's OWN output (its stderr is merged into stdout, so
# these flow through process_lines and get bad_json-dropped) that mean "the
# WebSocket never came up" — the failure that, un-surfaced, just looks like a
# silent router respawn loop. Lowercased for case-insensitive match.
_WS_FAIL_MARKERS = ("ws connect failed", "connect failed", "reconnect",
                    "persistent connection", "长连接")


def _diagnose_sidecar_exit(returncode, recent_lines) -> None:
    """When the sidecar dies, surface WHY — instead of its error output getting
    bad_json-dropped by process_lines and lost in a watchdog respawn loop. Prints
    the sidecar's last lines, and when they carry a WebSocket-connect-failure
    signature, the two console/proxy fixes that actually resolve it. Best-effort:
    never raises (a diagnostic must not add a second failure)."""
    try:
        tail = [l for l in list(recent_lines or []) if l.strip()][-12:]
    except RuntimeError:        # deque mutated mid-iteration by the writer thread
        tail = []
    if tail:
        print("     ↳ sidecar 最后输出：")
        for l in tail:
            print(f"       {l}")
    blob = "\n".join(tail).lower()
    if any(m in blob for m in _WS_FAIL_MARKERS):
        print("     ↳ 诊断：sidecar 的 WebSocket(长连接)没建起来。最常见两条：")
        print("       1) 该应用没开长连接订阅 → 飞书开发者后台 → 事件与回调 → 订阅方式")
        print("          → 改成「使用长连接接收事件/回调」(不是 Webhook URL)，保存。")
        print("       2) HTTPS_PROXY 挡了 WebSocket → 启动前 `export LARK_CLI_NO_PROXY=1`")
        print("          (或写进 $CLAUDETEAM_SECRETS_FILE / shell profile)。")


def _tee_recent(stream, sink):
    """Yield each line from `stream` while keeping the last N in `sink` (a bounded
    deque) — so a sidecar that dies mid-stream leaves its final output visible for
    `_diagnose_sidecar_exit` instead of it scrolling past as bad_json drops."""
    for line in stream:
        sink.append(line.rstrip("\n"))
        yield line


def _watch_subscribe_health(proc: subprocess.Popen, stop_event: threading.Event,
                            last_event_at: list[float],
                            events_seen: list[int],
                            recent_lines=None) -> None:
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
    # Short enough to detect a silent subscribe death in <30s, long
    # enough not to busy-loop. Toml-overridable via
    # router.subscribe_watchdog_period_s.
    period_s = float(tunables.tunable("router.subscribe_watchdog_period_s", 20.0))
    while not stop_event.wait(period_s):
        if proc.poll() is not None:
            print(f"  ⚠️ subscribe child exited (rc={proc.returncode}); router will exit so watchdog can respawn")
            _diagnose_sidecar_exit(proc.returncode, recent_lines)
            os.kill(os.getpid(), signal.SIGTERM)
            return
        idle = time.monotonic() - last_event_at[0]
        if idle > threshold:
            print(_subscribe_rotate_reason(idle, threshold, events_seen[0]))
            os.kill(os.getpid(), signal.SIGTERM)
            return


def _catchup_slash_fresh_ms() -> int:
    """Freshness threshold (ms) below which a catchup-delivered slash is
    treated as a live WS-fallback command (dispatch) rather than a stale
    backlog replay (suppress). Tunable; default 180s. <=0 is clamped to the
    default so a misconfig can't make everything 'stale' and re-eat slashes."""
    from claudeteam.runtime import tunables
    try:
        v = int(tunables.tunable("router.catchup_slash_fresh_ms", 600_000))
    except (TypeError, ValueError):
        v = 600_000
    return v if v > 0 else 180_000


def _notify_catchup_skips(default_target: str, *, dropped_stale: int,
                          slash_skipped: int) -> None:
    """After catchup, tell the routing-target agent (default manager) if the
    replay skipped anything — over-cap stale messages or un-replayed control
    commands — so it doesn't over-claim it received the whole backlog.
    No-op when nothing was skipped. Best-effort: a
    notice-write failure must never break bring-up."""
    if dropped_stale <= 0 and slash_skipped <= 0:
        return
    parts = []
    if dropped_stale > 0:
        parts.append(f"{dropped_stale} 条超上限的陈旧历史消息未重放")
    if slash_skipped > 0:
        parts.append(f"{slash_skipped} 条控制命令(/...)按规则未重放")
    note = ("⚠️ 恢复(catchup)时跳过了部分历史：" + "；".join(parts)
            + "。本次你**未必收到完整 backlog**——涉及老板原话/约束请 "
              "`task intent get` 现读或请老板重发，别按记忆脑补。")
    try:
        from claudeteam.store import local_facts
        local_facts.append_message(default_target, "router", note, priority="高")
    except Exception as e:
        warn(f"⚠️ catchup skip-notice failed: {e}")


def main(argv: list[str]) -> int:
    if maybe_print_help(argv, "usage: claudeteam router"):
        return 0

    chat = config.chat_id()
    if not chat:
        return error_exit("❌ chat_id not set in runtime_config.json")

    agents = config.agent_names()
    if not agents:
        return error_exit("❌ claudeteam.toml has no agents")

    pid_file = paths.router_pid_file()
    if not pidlock.acquire(pid_file, name="router"):
        return 1

    profile = config.lark_profile()
    cmd = _build_subscribe_cmd(profile)
    print(f"🚀 router subscribing on chat {chat} (profile={profile or '<default>'})")

    # ACP agents live as subprocesses of THIS daemon: the host consumes each
    # agent's delivery queue (written by deliver/send from any process),
    # drives the ACP turns, and ACKs rows. Started before the subscribe
    # child so queued backlog begins draining even while catchup runs.
    from claudeteam.runtime.acp_host import AcpHost
    acp_host_svc = AcpHost()
    acp_host_svc.start()

    # Standup ticker: while the team is actively working, nudge the manager
    # every standup.interval_minutes to inspect everyone and report progress
    # to the boss. Idle team → silent.
    from claudeteam.runtime.standup import StandupTicker
    standup_ticker = StandupTicker()
    standup_ticker.start()

    try:
        # Two precautions on the subscribe child:
        # - env=lark.subprocess_env() strips HTTPS_PROXY under LARK_CLI_NO_PROXY=1
        #   (lark-cli long-poll dies behind a proxy).
        # - start_new_session=True puts the npx → node → lark-cli chain in its
        #   own process group so SIGTERMing the router can kill the whole tree
        #   in one killpg call (otherwise grandchildren are orphaned).
        from claudeteam.util import detached_popen_kwargs
        proc = subprocess.Popen(
            cmd,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            text=True,
            bufsize=1,  # line-buffered
            env=lark.subprocess_env(),
            **detached_popen_kwargs(),
        )
    except FileNotFoundError:
        pidlock.release(pid_file)
        return error_exit("❌ npx / lark-cli not found in PATH")

    # Now that proc exists, install a SIGTERM handler that reaps the
    # subscribe group before exiting. (Plain sys.exit propagates SystemExit
    # past the except blocks, never running proc.terminate.)
    def _on_sigterm(*_):
        _terminate_subscribe_group(proc)
        standup_ticker.stop()
        acp_host_svc.stop()
        sys.exit(0)
    signal.signal(signal.SIGTERM, _on_sigterm)

    # Spawn the subscribe-health watchdog thread. It exits the daemon
    # cleanly if lark-cli dies under us — without it, process_lines would
    # block forever on stdout that npm-exec parent keeps open after the
    # lark-cli grandchild vanishes. Also self-terminates if events stop
    # flowing for too long (silent-subscribe-stall mode).
    stop_watchdog = threading.Event()
    last_event_at = [time.monotonic()]
    events_seen = [0]  # genuine handled events this process (idle vs stalled)
    # Bounded tail of the sidecar's raw output so that when it dies, the health
    # thread can show its last lines (the real WS-failure reason) instead of the
    # error scrolling past as bad_json drops. See _tee_recent / _diagnose_sidecar_exit.
    recent_lines: collections.deque = collections.deque(maxlen=30)
    threading.Thread(
        target=_watch_subscribe_health,
        args=(proc, stop_watchdog, last_event_at, events_seen, recent_lines),
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
        # already-handled messages.
        seen = _load_seen_msg_ids()

        def _bump_subscribe_alive():
            last_event_at[0] = time.monotonic()

        loop_kwargs = dict(
            team_agents=agents,
            chat_id=chat,
            default_target="manager",
            apply_fn=apply_fn,
            on_progress=_make_on_progress(last_event_at, events_seen),
            on_line_received=_bump_subscribe_alive,
            seen_msg_ids=seen,
        )

        # Catchup: replay anything newer than the cursor before going live
        catchup_meta: dict = {}
        try:
            pending = catchup.pending_lines(chat, profile=profile, meta=catchup_meta)
        except Exception as e:
            warn(f"⚠️  catchup fetch failed: {e}")
            pending = []
        if pending:
            print(f"📥 catching up {len(pending)} missed message(s)")
            # suppress_slash: drop a STALE replay or a destructive lifecycle
            # command (/restart, /shutdown 确认) from the backlog — replaying
            # those re-runs `down` and tears the team back down (F-G2-restore).
            # But a FRESH non-lifecycle slash arriving here is a WS-fallback
            # delivery of a live boss command (WS down) — it must dispatch,
            # not get eaten; the freshness threshold draws that line.
            cstats = process_lines(iter(pending), suppress_slash=True,
                                   catchup_slash_fresh_ms=_catchup_slash_fresh_ms(),
                                   **loop_kwargs)
            # Tell the routing-target agent if the
            # replay skipped anything (over-cap stale / un-replayed control
            # commands) so it doesn't over-claim it got the whole backlog.
            _notify_catchup_skips(
                loop_kwargs["default_target"],
                dropped_stale=catchup_meta.get("dropped_stale", 0),
                slash_skipped=cstats.drops_by_reason.get("catchup_slash_skip", 0))

        stats = process_lines(_tee_recent(proc.stdout, recent_lines), **loop_kwargs)
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
        standup_ticker.stop()
        acp_host_svc.stop()
        pidlock.release(pid_file)
