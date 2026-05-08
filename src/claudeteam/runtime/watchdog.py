"""Single-file process supervisor.

Each ProcessSpec describes a daemon to keep alive; a single
`supervise()` sweep checks every spec, spawns or backs off as
appropriate, and writes one heartbeat line.

State machine per spec:
    alive             → noop
    dead, ok-to-spawn → respawn
    dead, in cooldown → noop, count down
    respawn fails N times → enter cooldown for cooldown_secs
                            + alert_fn(name, fail_count, cooldown_secs)
                            (optional — `commands/watchdog.py` wires
                            this to a red Feishu card)

Liveness is `kill(pid, 0) AND cmdline contains expected_cmdline`.
The cmdline check defends against PID reuse.

Orphan reap: each ProcessSpec carries `orphan_markers: tuple[str, ...]`.
Before `respawn` Popen's the new daemon, `reap_orphans(spec)` scans
`ps -eo pid,ppid,command` for processes with PPID=1 whose cmdline
contains all markers, and SIGTERMs them. This kills lark-cli
`+subscribe` zombies left by a SIGKILL'd predecessor router so the
new daemon's subscribe doesn't race the orphan for events.
Best-effort: any subprocess error / missing ps / non-zero return /
fake Popen yields no orphans.
"""
from __future__ import annotations

import os
import signal
import subprocess
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Callable

from claudeteam.runtime import paths, pidlock


# ── per-process spec & state ──────────────────────────────────────


@dataclass(frozen=True)
class ProcessSpec:
    name: str                 # human-readable label, e.g. "router"
    pid_file: Path            # where the daemon writes its pid
    expected_cmdline: str     # substring that must appear in /proc/<pid>/cmdline
    spawn_cmd: list[str]      # subprocess.Popen argv to (re)start the daemon
    max_retries: int = 3
    cooldown_secs: int = 600
    # If set, before respawning this spec the watchdog scans for processes
    # whose command line contains all of these substrings AND whose PPID
    # is 1, and SIGTERMs them. Use to reap orphaned subprocess children
    # (e.g. lark-cli `event +subscribe`) left behind by a SIGKILL'd
    # predecessor — without it, the new daemon's subscribe runs in
    # parallel with the orphan and Feishu randomly splits events between
    # them. Empty tuple = no reap.
    orphan_markers: tuple[str, ...] = ()
    # If set, respawn redirects the child's stdout+stderr (append) to this
    # path instead of DEVNULL. Without this, transient failures inside the
    # daemon are unobservable: the router silently dropping a slash event
    # or the watchdog hitting a Popen error leaves no trace. Append-mode so
    # multi-respawn history accumulates; operator rotates by hand.
    log_file: Path | None = None


@dataclass
class ProcessState:
    name: str
    fail_count: int = 0
    cooldown_until: float = 0.0
    last_action: str = ""     # "alive" / "respawned" / "cooldown" / "fail"


# ── liveness check ────────────────────────────────────────────────


def _read_cmdline(pid: int) -> str:
    try:
        # Linux /proc; on macOS /proc doesn't exist so we fall back to ps
        path = f"/proc/{pid}/cmdline"
        if os.path.exists(path):
            with open(path, "rb") as fh:
                return fh.read().decode("utf-8", errors="ignore").replace("\0", " ")
        # macOS fallback
        r = subprocess.run(["ps", "-p", str(pid), "-o", "command="],
                           capture_output=True, text=True, timeout=3)
        return r.stdout.strip() if r.returncode == 0 else ""
    except (OSError, subprocess.TimeoutExpired):
        return ""


def is_alive(spec: ProcessSpec, *,
             read_pid: Callable = pidlock.read_pid,
             pid_alive: Callable = pidlock.pid_alive,
             read_cmdline: Callable = _read_cmdline) -> bool:
    pid = read_pid(spec.pid_file)
    if pid is None or not pid_alive(pid):
        return False
    return spec.expected_cmdline in read_cmdline(pid)


# ── respawn ────────────────────────────────────────────────────────


def list_orphan_pids(markers: tuple[str, ...], *,
                     run: Callable = subprocess.run) -> list[int]:
    """PIDs of processes whose command line contains every marker AND
    whose PPID is 1 (orphaned to init/launchd).

    Scans `ps -eo pid,ppid,command`. Returns [] if the markers tuple is
    empty, or if `ps` fails / times out / produces malformed lines —
    orphan-reap is best-effort, not load-bearing for liveness.
    """
    if not markers:
        return []
    try:
        r = run(["ps", "-eo", "pid,ppid,command"],
                capture_output=True, text=True, timeout=5)
    except (FileNotFoundError, subprocess.TimeoutExpired, OSError,
            AttributeError):
        # AttributeError covers test fakes that don't fully implement
        # Popen — orphan reap is best-effort, never load-bearing for
        # liveness, so swallow and bail.
        return []
    if r is None or r.returncode != 0:
        return []
    orphans: list[int] = []
    for line in r.stdout.splitlines()[1:]:  # skip "PID PPID COMMAND" header
        parts = line.strip().split(None, 2)
        if len(parts) < 3:
            continue
        try:
            pid = int(parts[0])
            ppid = int(parts[1])
        except ValueError:
            continue
        if ppid != 1:
            continue
        cmd = parts[2]
        if all(m in cmd for m in markers):
            orphans.append(pid)
    return orphans


def reap_orphans(spec: ProcessSpec, *,
                 run: Callable = subprocess.run,
                 kill: Callable = os.kill,
                 log: Callable = print) -> int:
    """SIGTERM any orphan processes matching `spec.orphan_markers`.

    Idempotent — subsequent calls find nothing once reaped. Safe to call
    even when there's no risk of orphans (returns 0). ProcessLookupError
    and PermissionError are swallowed: the process exited between scan
    and kill, or belongs to a different uid (rare on a single-user box,
    impossible to clean up anyway).
    """
    pids = list_orphan_pids(spec.orphan_markers, run=run)
    reaped = 0
    for pid in pids:
        try:
            kill(pid, signal.SIGTERM)
            reaped += 1
        except (ProcessLookupError, PermissionError):
            continue
    if reaped:
        log(f"  ♻️  reaped {reaped} orphan {spec.name} subprocess(es)")
    return reaped


def respawn(spec: ProcessSpec, *,
            popen: Callable | None = None,
            reap: Callable = reap_orphans) -> bool:
    """Spawn `spec` detached. Returns True on launch, False on OSError.

    Before spawning, reap any orphan subprocess children matching
    `spec.orphan_markers` — without this, a SIGKILL'd previous daemon's
    children (e.g. lark-cli `event +subscribe`) would run in parallel
    with the new daemon's children and split events between them.

    `popen` is resolved at call time (not as a default-arg) so callers
    that monkeypatch `subprocess.Popen` for tests intercept this call
    too — `claudeteam up`'s test rig relies on that.
    """
    reap(spec)
    runner = popen if popen is not None else subprocess.Popen
    log_fh = None
    if spec.log_file is not None:
        try:
            spec.log_file.parent.mkdir(parents=True, exist_ok=True)
            log_fh = open(spec.log_file, "a", buffering=1)
        except OSError as e:
            print(f"  ⚠️ {spec.name} log_file open failed ({e}); falling back to DEVNULL")
            log_fh = None
    stdout = log_fh if log_fh is not None else subprocess.DEVNULL
    stderr = log_fh if log_fh is not None else subprocess.DEVNULL
    # PYTHONUNBUFFERED forces line-buffer on the child's sys.stdout when
    # it's a regular file. Without it, daemon prints stay stuck in
    # Python's stdio block buffer and the operator sees an empty
    # router.log even though the daemon is running fine. Round 2 host
    # smoke caught this 2026-05-07: router PID alive, fd points at
    # router.log, but file size 0 indefinitely.
    env = os.environ.copy()
    env["PYTHONUNBUFFERED"] = "1"
    try:
        runner(spec.spawn_cmd, start_new_session=True,
               stdout=stdout, stderr=stderr, env=env)
        return True
    except (OSError, ValueError) as e:
        print(f"  ⚠️ {spec.name} respawn failed: {e}")
        return False
    finally:
        # Popen dup'd the fd at fork; closing our local handle is safe and
        # keeps the watchdog from holding the file open across respawns.
        if log_fh is not None:
            try:
                log_fh.close()
            except OSError:
                pass


# ── one sweep ──────────────────────────────────────────────────────


def supervise(specs: list[ProcessSpec],
              states: dict[str, ProcessState], *,
              now: Callable = time.time,
              alive_check: Callable = is_alive,
              respawn_fn: Callable = respawn,
              alert_fn: Callable | None = None,
              log: Callable = print) -> None:
    """Walk every spec once, decide alive / respawn / cooldown.

    Mutates `states` in place. Caller wraps this in their own loop.

    Round-82: when a spec enters cooldown (max_retries respawns failed),
    invoke `alert_fn(spec_name, fail_count, cooldown_secs)` so callers
    can fan out to a Feishu chat / pager / log file. Default = None
    means no alert (preserves backward compat). The router daemon wires
    `alert_fn` to `feishu/chat.send_text` so boss sees daemon death
    in chat the moment cooldown begins.

    `alert_fn` exceptions are caught (best-effort): a broken alert path
    must not stop supervise from doing its primary job (state machine).
    """
    t = now()
    for spec in specs:
        st = states.setdefault(spec.name, ProcessState(spec.name))

        # cooldown: skip until expiry
        if t < st.cooldown_until:
            st.last_action = "cooldown"
            continue

        if alive_check(spec):
            if st.last_action != "alive":
                log(f"✅ {spec.name} alive")
            st.fail_count = 0
            st.last_action = "alive"
            continue

        # dead: respawn
        if respawn_fn(spec):
            log(f"🔁 {spec.name} respawned (fail_count was {st.fail_count})")
            st.last_action = "respawned"
            # don't reset fail_count yet — wait until next sweep proves it stuck
            continue

        st.fail_count += 1
        if st.fail_count >= spec.max_retries:
            st.cooldown_until = t + spec.cooldown_secs
            failed_at = st.fail_count
            st.fail_count = 0  # reset for after cooldown
            st.last_action = "cooldown"
            log(f"⛔ {spec.name} entering {spec.cooldown_secs}s cooldown after {spec.max_retries} fails")
            if alert_fn is not None:
                try:
                    alert_fn(spec.name, failed_at, spec.cooldown_secs)
                except Exception as e:
                    log(f"  ⚠️ alert_fn raised on {spec.name} cooldown: {e}")
        else:
            st.last_action = "fail"
            log(f"❌ {spec.name} respawn failed ({st.fail_count}/{spec.max_retries})")


# ── default specs for ClaudeTeam ──────────────────────────────────


def _claudeteam_spec(name: str, pid_file: Path, *,
                     orphan_markers: tuple[str, ...] = (),
                     log_file: Path | None = None) -> ProcessSpec:
    """Build a ProcessSpec for a `claudeteam <name>` daemon. The cmdline-match
    string is just `\"claudeteam\"` so any process whose argv contains the
    word counts — defends against PID reuse, doesn't lock to argv shape."""
    return ProcessSpec(
        name=name,
        pid_file=pid_file,
        expected_cmdline="claudeteam",
        spawn_cmd=["claudeteam", name],
        orphan_markers=orphan_markers,
        log_file=log_file,
    )


# Markers identifying an orphaned lark-cli `event +subscribe` chain
# left behind when a previous router daemon was SIGKILL'd before its
# SIGTERM handler could reap the subscribe group. The npm-exec parent
# of the chain reparents to PID 1; matching it (rather than the deeper
# node/lark-cli children) reaps the entire group on SIGTERM.
_ROUTER_SUBSCRIBE_MARKERS = ("@larksuite/cli", "+subscribe")


def default_specs() -> list[ProcessSpec]:
    """Specs the watchdog supervises. Just the router — the watchdog
    doesn't supervise itself."""
    return [_claudeteam_spec("router", paths.router_pid_file(),
                             orphan_markers=_ROUTER_SUBSCRIBE_MARKERS,
                             log_file=paths.router_log_file())]


def all_known_specs() -> list[ProcessSpec]:
    """Every daemon ClaudeTeam ships, for `health` and similar audits.
    Includes the watchdog itself so health can verify its lock file
    matches a live process."""
    return [
        _claudeteam_spec("router", paths.router_pid_file(),
                         orphan_markers=_ROUTER_SUBSCRIBE_MARKERS,
                         log_file=paths.router_log_file()),
        _claudeteam_spec("watchdog", paths.watchdog_pid_file(),
                         log_file=paths.watchdog_log_file()),
    ]
