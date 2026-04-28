"""Unified agent state classification for tmux-backed CLI agents."""
from __future__ import annotations

import dataclasses
import re
import subprocess
import time
from collections import defaultdict, deque

from claudeteam.cli_adapters import adapter_for_agent
from claudeteam.runtime.tmux_utils import (
    capture_pane,
    detect_unsubmitted_input_text,
)


@dataclasses.dataclass
class AgentState:
    agent: str
    emoji: str
    code: str
    brief: str
    confidence: str
    live_cli: bool
    idle_hint: bool | None


_ANSI_RE = re.compile(r"\x1b\[[0-9;?]*[ -/]*[@-~]")
_BRAILLE_SPINNER = "⣾⣽⣻⢿⡿⣟⣯⣷"
_ARC_SPINNER = "◐◑◒◓"
_GENERIC_READY_MARKERS = (
    "bypass permissions on",
    "? for shortcuts",
    "new task?",
    "Send /help for help information",
    "Implement {feature}",
    "Find and fix a bug in @filename",
    "tab to queue message",
)
_GENERIC_BUSY_MARKERS = (
    "esc to interrupt",
    "Thinking",
    "Running tool",
    *_BRAILLE_SPINNER,
    *_ARC_SPINNER,
)
_PERMISSION_RE = re.compile(
    r"do you want to proceed\?|would you like to .+\?|\b(always allow|allow|deny)\b|"
    r"\b(yes|no)\b|❯\s*\d+\.|^\s*\d+\.\s*(yes|no)\b|"
    r"approval required|approve this|proceed\?",
    re.I | re.M,
)
_LIMIT_RE = re.compile(
    r"hit your limit|usage limit|rate limit|monthly limit|try again later|"
    r"quota exceeded|too many requests",
    re.I,
)
_COMPACT_RE = re.compile(
    r"compacting conversation|compacting…|conversation summary|context limit",
    re.I,
)
_LOADING_RE = re.compile(
    r"loading configuration|resuming conversation|starting claude code|starting .*cli|resume session",
    re.I,
)
_AUTH_RE = re.compile(r"auth(?:entication)? required|login required|please log in|sign in", re.I)
_UPDATE_RE = re.compile(r"update available|install update|new version|upgrade required", re.I)
_SHELL_PROMPT_RE = re.compile(
    r"(?:^|\n)\s*(?:[\w.-]+@[^\s:]+:[^\n]*[$#]|root@[0-9a-f]+:[^\n#]*#)\s*$"
)
_EMPTY_PROMPT_RE = re.compile(r"^\s*(?:[>❯›]\s*|[│┃]\s*[>❯›]\s*)$")
_INPUT_PROMPT_RE = re.compile(r"^\s*(?:[>❯›]|[│┃]\s*[>❯›]|input\s*[:：]|prompt\s*[:：])\s*", re.I)
_SLEEP_RE = re.compile(r"💤.*已休眠|已休眠|lazy-wake suspend|待 wake", re.I)


def _strip_control(text: str) -> str:
    return _ANSI_RE.sub("", text or "").replace("\r", "")


def _nonempty_lines(text: str) -> list[str]:
    return [line.rstrip() for line in _strip_control(text).splitlines() if line.strip()]


def _tail(text: str, lines: int) -> str:
    return "\n".join(_nonempty_lines(text)[-lines:])


def _tail_summary(text: str, max_len: int = 40) -> str:
    lines = _nonempty_lines(text)
    return (lines[-1].strip() if lines else "")[:max_len]


def _ready_markers(agent: str) -> list[str]:
    try:
        return list(adapter_for_agent(agent).ready_markers()) + list(_GENERIC_READY_MARKERS)
    except Exception:
        return list(_GENERIC_READY_MARKERS)


def _busy_markers(agent: str) -> list[str]:
    try:
        return list(adapter_for_agent(agent).busy_markers()) + list(_GENERIC_BUSY_MARKERS)
    except Exception:
        return list(_GENERIC_BUSY_MARKERS)


def _process_name(agent: str) -> str:
    try:
        return adapter_for_agent(agent).process_name()
    except Exception:
        return "claude"


def _state(agent: str, emoji: str, code: str, brief: str, confidence: str,
           live_cli: bool, idle_hint: bool | None) -> AgentState:
    return AgentState(agent, emoji, code, brief, confidence, live_cli, idle_hint)


def _status_table_state(agent: str) -> str | None:
    try:
        from claudeteam.storage.local_facts import get_status
        row = get_status(agent) or {}
        return row.get("status") or None
    except Exception:
        return None


def _window_exists(session: str, agent: str) -> bool:
    try:
        r = subprocess.run(
            ["tmux", "has-session", "-t", f"{session}:{agent}"],
            capture_output=True,
            timeout=3,
        )
        return r.returncode == 0
    except Exception:
        return False


def _pane_pid(session: str, agent: str) -> int | None:
    try:
        r = subprocess.run(
            ["tmux", "display-message", "-t", f"{session}:{agent}", "-p", "#{pane_pid}"],
            capture_output=True,
            text=True,
            timeout=3,
        )
        if r.returncode != 0:
            return None
        return int((r.stdout or "").strip())
    except Exception:
        return None


def _capture_pane_raw(session: str, agent: str, lines: int = 80) -> str:
    try:
        r = subprocess.run(
            ["tmux", "capture-pane", "-e", "-t", f"{session}:{agent}", "-p", "-S", f"-{lines}"],
            capture_output=True,
            text=True,
            timeout=3,
        )
        return r.stdout if r.returncode == 0 else ""
    except Exception:
        return ""


def pane_diff_classify(session, agent, samples=10, interval=0.5) -> bool:
    previous = None
    for idx in range(max(1, samples)):
        current = _capture_pane_raw(session, agent)
        if previous is not None and current != previous:
            return True
        previous = current
        if idx < samples - 1:
            time.sleep(interval)
    return False


def _process_tree() -> tuple[dict[int, tuple[int, str]], dict[int, list[int]]]:
    try:
        r = subprocess.run(
            ["ps", "-eo", "pid=,ppid=,comm="],
            capture_output=True,
            text=True,
            timeout=3,
        )
    except Exception:
        return {}, {}
    if r.returncode != 0:
        return {}, {}
    procs: dict[int, tuple[int, str]] = {}
    children: dict[int, list[int]] = defaultdict(list)
    for line in (r.stdout or "").splitlines():
        parts = line.split(None, 2)
        if len(parts) != 3:
            continue
        try:
            pid = int(parts[0])
            ppid = int(parts[1])
        except ValueError:
            continue
        comm = parts[2].strip()
        procs[pid] = (ppid, comm)
        children[ppid].append(pid)
    return procs, children


def _subtree_has_process(root_pid: int | None, process_name: str) -> bool:
    if root_pid is None:
        return False
    procs, children = _process_tree()
    if root_pid not in procs:
        return False
    expected = process_name.lower()
    q = deque([root_pid])
    seen: set[int] = set()
    while q:
        pid = q.popleft()
        if pid in seen:
            continue
        seen.add(pid)
        comm = procs.get(pid, (0, ""))[1].lower()
        if comm == expected or comm.startswith(expected) or expected in comm:
            return True
        q.extend(children.get(pid, []))
    return False


def _has_ready_marker(agent: str, text: str) -> bool:
    low = text.lower()
    return any(marker.lower() in low for marker in _ready_markers(agent))


def _has_current_empty_prompt(text: str) -> bool:
    lines = _nonempty_lines(text)
    return bool(lines and _EMPTY_PROMPT_RE.match(lines[-1].strip()))


def _has_current_input_prompt(text: str) -> bool:
    lines = _nonempty_lines(text)
    return bool(lines and _INPUT_PROMPT_RE.match(lines[-1].strip()))


def _has_current_busy(agent: str, text: str) -> bool:
    current = _tail(text, 8)
    if not current:
        return False
    if _has_current_empty_prompt(current) or _has_ready_marker(agent, current):
        return False
    low = current.lower()
    return any(marker.lower() in low for marker in _busy_markers(agent))


def classify_pane(agent: str, pane_text: str, *, live_cli: bool,
                  idle_hint: bool | None = None,
                  window_exists: bool = True,
                  status_table_state: str | None = None) -> AgentState:
    text = _strip_control(pane_text)
    tail40 = _tail(text, 40)
    status_state = status_table_state or ""

    if not window_exists or not text:
        return _state(agent, "⬜", "no_window", "无窗口", "high", False, None)

    if not live_cli:
        if status_state == "休眠" or _SLEEP_RE.search(tail40):
            return _state(agent, "💤", "sleep", "休眠", "high", False, idle_hint)
        return _state(agent, "🛑", "cli_not_running", "CLI 未运行（shell）", "high", False, idle_hint)

    mismatch = "（状态表休眠不一致）" if status_state == "休眠" else ""

    if _AUTH_RE.search(tail40):
        return _state(agent, "🔐", "auth_required", f"待登录{mismatch}", "high", True, idle_hint)
    if _UPDATE_RE.search(tail40):
        return _state(agent, "⬆️", "update_required", f"待更新{mismatch}", "high", True, idle_hint)
    if _LOADING_RE.search(tail40):
        return _state(agent, "🌅", "waking", f"唤醒中{mismatch}", "high", True, idle_hint)
    if _SHELL_PROMPT_RE.search(tail40):
        return _state(agent, "❌", "startup_error", f"启动异常（shell prompt）{mismatch}", "medium", True, idle_hint)
    if _PERMISSION_RE.search(tail40):
        return _state(agent, "⚠️", "permission", f"等权限{mismatch}", "high", True, idle_hint)
    if _LIMIT_RE.search(tail40):
        return _state(agent, "⛔", "quota", f"quota/限流{mismatch}", "high", True, idle_hint)
    if _COMPACT_RE.search(tail40):
        return _state(agent, "🗜️", "compact", f"压缩中{mismatch}", "high", True, idle_hint)
    if idle_hint is False or _has_current_busy(agent, text):
        return _state(agent, "🔄", "busy", f"工作中{mismatch}", "medium", True, idle_hint)

    residual = detect_unsubmitted_input_text(text)
    if residual:
        return _state(agent, "🧷", "unsubmitted_input", f"输入未提交{mismatch}", "high", True, idle_hint)

    if _has_current_empty_prompt(text) or _has_ready_marker(agent, tail40):
        brief = "待命" if status_state == "进行中" else "idle"
        if status_state == "进行中":
            brief += "（状态表滞后）"
        brief += mismatch
        return _state(agent, "✅" if brief.startswith("待命") else "💤", "idle", brief, "high", True, True if idle_hint is None else idle_hint)

    tail = _tail_summary(text)
    return _state(agent, "🔘", "unknown", tail or "未知", "low", True, idle_hint)


def classify(agent: str, session: str) -> AgentState:
    if not _window_exists(session, agent):
        return _state(agent, "⬜", "no_window", "无窗口", "high", False, None)

    pane_text = capture_pane(session, agent, lines=80)
    status_state = _status_table_state(agent)
    pane_pid = _pane_pid(session, agent)
    live_cli = _subtree_has_process(pane_pid, _process_name(agent))
    initial = classify_pane(
        agent,
        pane_text,
        live_cli=live_cli,
        idle_hint=None,
        window_exists=True,
        status_table_state=status_state,
    )
    if not live_cli or initial.code in {
        "auth_required",
        "update_required",
        "waking",
        "startup_error",
        "permission",
        "quota",
        "compact",
        "unsubmitted_input",
    }:
        return initial

    diff_busy = pane_diff_classify(session, agent)
    if diff_busy:
        mismatch = "（状态表休眠不一致）" if status_state == "休眠" else ""
        return _state(agent, "🔄", "busy", f"工作中{mismatch}", "high", True, False)

    brief = "待命" if status_state == "进行中" else "idle"
    if status_state == "进行中":
        brief += "（状态表滞后）"
    if status_state == "休眠":
        brief += "（状态表休眠不一致）"
    return _state(agent, "✅" if brief.startswith("待命") else "💤", "idle", brief, "high", True, True)
