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
    """Return PIDs of CLI children under the agent's tmux pane shell."""
    bash_pid = _pane_bash_pid(agent_name, tmux_session)
    if bash_pid is None:
        return []
    proc_name = get_process_name(agent_name)
    result = []
    for proc_dir in glob.glob("/proc/[0-9]*"):
        try:
            with open(f"{proc_dir}/status") as f:
                status = f.read()
        except OSError:
            continue
        if f"\nPPid:\t{bash_pid}\n" not in status:
            continue
        try:
            with open(f"{proc_dir}/comm") as f:
                comm = f.read().strip()
        except OSError:
            continue
        if comm == proc_name:
            try:
                result.append(int(os.path.basename(proc_dir)))
            except ValueError:
                pass
    return result


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
) -> bool:
    """Poll tmux pane until CLI UI ready markers appear."""
    markers = get_ready_markers(agent_name)
    proc_name = get_process_name(agent_name)
    not_ready = ("Loading configuration",)
    deadline = time.time() + timeout_s
    while time.time() < deadline:
        pane = capture_pane_fn(agent_name)
        tail = "\n".join(pane.splitlines()[-30:])
        if any(m in tail for m in not_ready):
            time.sleep(0.5)
            continue
        if any(m in tail for m in markers):
            if proc_name == "kimi":
                time.sleep(1.5)
            return True
        time.sleep(0.5)
    return False


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
        return wait_ready(agent_name, timeout_s=min(10, WAKE_READY_TIMEOUT_S))

    now = time.time()
    spawn_thread = False
    with _wake_lock:
        st = _wake_state.get(agent_name)
        if st and (now - st["started_at"]) * 1000 < WAKE_DEBOUNCE_MS \
                and not st["ready_event"].is_set():
            ev = st["ready_event"]
        else:
            ev = threading.Event()
            _wake_state[agent_name] = {"started_at": now, "ready_event": ev}
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
                        print(f"  ⚠️ wake_on_deliver: lifecycle wake "
                              f"{agent_name} 退出 {r.returncode}: "
                              f"{(r.stderr or '').strip()[:200]}")
                    wait_ready(agent_name)
            except subprocess.TimeoutExpired:
                print(f"  ⚠️ wake_on_deliver: lifecycle wake {agent_name} 超时")
            except Exception as e:
                print(f"  ⚠️ wake_on_deliver: {agent_name} 异常: {e}")
            finally:
                ev.set()

        threading.Thread(target=_do_wake, daemon=True).start()

    return ev.wait(WAKE_READY_TIMEOUT_S + 5)
