"""Agent wake-on-delivery for the router daemon.

When a sleeping agent receives a message the router must first revive its
CLI process (via agent_lifecycle.sh wake) and wait for the UI to be ready
before injecting the message into the tmux pane.

Concurrency contract:
  - At most WAKE_MAX_PARALLEL agents waking simultaneously (Semaphore).
  - 500 ms debounce: multiple messages for the same agent within 500 ms
    share one wake, not N separate restarts.
  - Caller blocks until wake completes or WAKE_READY_TIMEOUT_S elapses.
"""
from __future__ import annotations

import glob
import os
import subprocess
import threading
import time
from typing import List, Optional

from claudeteam.runtime.tmux_utils import wait_cli_ui_ready as _wait_cli_ui_ready


WAKE_DEBOUNCE_MS = 500
WAKE_READY_TIMEOUT_S = 30
WAKE_MAX_PARALLEL = 2

_wake_sem = threading.Semaphore(WAKE_MAX_PARALLEL)
_wake_lock = threading.Lock()
_wake_state: dict = {}   # agent_name -> {"started_at": float, "ready_event": Event}


def agent_has_live_cli(
    agent_name: str,
    tmux_session: str,
    *,
    get_process_name: callable,
) -> bool:
    """Return True if agent's tmux pane has a live CLI child process."""
    return len(cli_pids_in_pane(agent_name, tmux_session, get_process_name=get_process_name)) > 0


def cli_pids_in_pane(
    agent_name: str,
    tmux_session: str,
    *,
    get_process_name: callable,
) -> List[int]:
    """Return PIDs of CLI descendants under the agent's tmux pane shell."""
    bash_pid = _pane_bash_pid(agent_name, tmux_session)
    if bash_pid is None:
        return []
    proc_name = get_process_name(agent_name)
    children: dict[int, list[int]] = {}
    comms: dict[int, str] = {}
    for proc_dir in glob.glob("/proc/[0-9]*"):
        try:
            pid = int(os.path.basename(proc_dir))
            with open(f"{proc_dir}/status") as f:
                status = f.read()
        except (OSError, ValueError):
            continue
        ppid = _parse_ppid(status)
        if ppid is None:
            continue
        children.setdefault(ppid, []).append(pid)
        try:
            with open(f"{proc_dir}/comm") as f:
                comms[pid] = f.read().strip()
        except OSError:
            pass

    result = []
    stack = list(children.get(bash_pid, []))
    seen = set()
    while stack:
        pid = stack.pop()
        if pid in seen:
            continue
        seen.add(pid)
        if comms.get(pid) == proc_name:
            result.append(pid)
        stack.extend(children.get(pid, []))
    return result


def _parse_ppid(status: str) -> Optional[int]:
    for line in status.splitlines():
        if line.startswith("PPid:"):
            try:
                return int(line.split()[1])
            except (IndexError, ValueError):
                return None
    return None


def _pane_bash_pid(agent_name: str, tmux_session: str) -> Optional[int]:
    try:
        r = subprocess.run(
            ["tmux", "display-message", "-t", f"{tmux_session}:{agent_name}",
             "-p", "#{pane_pid}"],
            capture_output=True, text=True, timeout=5,
        )
        if r.returncode != 0:
            return None
        return int(r.stdout.strip())
    except (ValueError, Exception):
        return None


def wait_cli_ui_ready(
    agent_name: str,
    *,
    capture_pane_fn: callable,
    get_ready_markers: callable,
    get_process_name: callable,
    timeout_s: float = WAKE_READY_TIMEOUT_S,
):
    """Poll tmux pane until CLI UI ready markers appear, returning WakeReadyResult."""
    return _wait_cli_ui_ready(
        lambda: capture_pane_fn(agent_name),
        get_ready_markers(agent_name),
        process_name=get_process_name(agent_name),
        timeout_s=timeout_s,
    )


def wake_on_deliver(
    agent_name: str,
    lifecycle_sh: str,
    *,
    has_live_cli: callable,
    wait_ready: callable,
) -> bool:
    """Wake a sleeping agent and wait for its UI to be ready.

    Idempotent + concurrency-safe. Returns True when agent is ready.
    """
    if has_live_cli(agent_name):
        ready = wait_ready(agent_name, timeout_s=min(10, WAKE_READY_TIMEOUT_S))
        if not ready:
            print(f"  ⚠️ wake_on_deliver: {agent_name} live CLI not ready: {ready.reason}")
        return bool(ready)

    now = time.time()
    spawn_thread = False
    with _wake_lock:
        st = _wake_state.get(agent_name)
        if st and (now - st["started_at"]) * 1000 < WAKE_DEBOUNCE_MS \
                and not st["ready_event"].is_set():
            ev = st["ready_event"]
        else:
            ev = threading.Event()
            st = {"started_at": now, "ready_event": ev, "ok": False, "reason": "wake_in_progress"}
            _wake_state[agent_name] = st
            spawn_thread = True

    if spawn_thread:
        def _do_wake():
            try:
                with _wake_sem:
                    print(f"  🌅 wake_on_deliver: 唤醒 {agent_name}")
                    r = subprocess.run(
                        ["bash", lifecycle_sh, "wake", agent_name],
                        capture_output=True, text=True, timeout=20,
                    )
                    if r.returncode != 0:
                        st["reason"] = "wake_failed"
                        print(f"  ⚠️ wake_on_deliver: lifecycle wake "
                              f"{agent_name} 退出 {r.returncode}: "
                              f"{(r.stderr or '').strip()[:200]}")
                        return
                    ready = wait_ready(agent_name)
                    st["ok"] = bool(ready)
                    st["reason"] = ready.reason
                    if not ready:
                        print(f"  ⚠️ wake_on_deliver: {agent_name} UI 未 ready: {ready.reason}")
            except subprocess.TimeoutExpired:
                st["reason"] = "wake_timeout"
                print(f"  ⚠️ wake_on_deliver: lifecycle wake {agent_name} 超时")
            except Exception as e:
                st["reason"] = "unknown_error"
                print(f"  ⚠️ wake_on_deliver: {agent_name} 异常: {e}")
            finally:
                ev.set()

        threading.Thread(target=_do_wake, daemon=True).start()

    if not ev.wait(WAKE_READY_TIMEOUT_S + 5):
        return False
    return bool(st.get("ok"))
