#!/usr/bin/env python3
"""
tmux 工具函数 — 检测 Agent 空闲状态后安全注入文本

主要函数:
  capture_pane(session, window)          → 获取 pane 当前可见内容
  is_agent_idle(session, window)         → 判断 Agent 是否处于空闲（可接受输入）
  send_ctrlc(session, window)            → 发送 Ctrl+C 中断当前进程
  inject_when_idle(session, window, text) → 等待空闲后安全注入文本

空闲检测原理:
  Claude Code 空闲时末尾可见 "> " / "❯ " 等提示符，忙碌时显示旋转符号（⣾⣽…）
  或"Thinking"等流式输出特征。inject_when_idle 轮询最多 wait_secs 秒，
  确认空闲后用 send-keys -l（字面模式）注入，避免 # $ 等字符被 tmux/shell 解释。
"""
import contextlib
import dataclasses
import re
import subprocess, time, os, tempfile

# ── 状态特征字符串 ─────────────────────────────────────────────

# 出现这些 → 认为忙碌（正在处理，需要等待）
_BUSY_MARKERS = [
    "⣾", "⣽", "⣻", "⢿", "⡿", "⣟", "⣯", "⣷",  # braille spinner
    "◐", "◑", "◒", "◓",                            # arc spinner
    "Thinking", "Running tool",                      # Claude Code 状态文字
    # 注意：移除 "…" — 太常见，历史输出中大量出现，容易误判为忙碌
]

_INPUT_PROMPT_RE = re.compile(
    r"^\s*(?:[>❯›]\s+|[│┃]\s*[>❯›]\s+|(?:input|prompt)\s*[:：]\s+)(?P<text>.+?)\s*$",
    re.I,
)
_READY_PLACEHOLDERS = (
    "tab to queue message",
    "? for shortcuts",
    "Send /help for help information",
    "Implement {feature}",
    "Summarize recent commits",
    "Find and fix a bug in @filename",
    "Use /skills to list available skills",
    "Explain this codebase",
)


@dataclasses.dataclass
class InjectionResult:
    ok: bool
    submitted: bool = False
    busy_before: bool = False
    residual_visible: bool = False
    unsafe_input: bool = False
    forced: bool = False
    error: str = ""
    method: str = ""
    target: str = ""
    tail_summary: str = ""

    def __bool__(self):
        return self.ok and self.submitted and not self.unsafe_input and not self.error

# ── 核心函数 ──────────────────────────────────────────────────

def capture_pane(session, window):
    """
    获取 tmux pane 当前可见内容，返回字符串。
    失败（窗口不存在等）返回空字符串。
    """
    try:
        r = subprocess.run(
            ["tmux", "capture-pane", "-t", f"{session}:{window}", "-p"],
            capture_output=True, text=True, timeout=5
        )
        return r.stdout if r.returncode == 0 else ""
    except Exception:
        return ""


def _strip_control(text):
    text = re.sub(r"\x1b\[[0-9;?]*[ -/]*[@-~]", "", text or "")
    return text.replace("\r", "")


def _tail_summary(text, max_len=240):
    return " ".join(_strip_control(text).split())[-max_len:]


def detect_unsubmitted_input_text(pane_text):
    """Return residual input text if the visible prompt appears non-empty."""
    lines = _strip_control(pane_text).rstrip().splitlines()
    for raw in reversed(lines[-8:]):
        s = raw.strip()
        if not s:
            continue
        m = _INPUT_PROMPT_RE.match(s)
        if not m:
            if any(ph in s for ph in _READY_PLACEHOLDERS):
                return ""
            continue
        text = m.group("text").strip()
        if not text or any(ph in text for ph in _READY_PLACEHOLDERS):
            return ""
        return text
    return ""


def has_unsubmitted_input(session, window):
    return bool(detect_unsubmitted_input_text(capture_pane(session, window)))


@contextlib.contextmanager
def _pane_inject_lock(session, window, timeout=10):
    lock_dir = os.path.join(os.path.dirname(__file__), "..", "workspace",
                            "shared", ".inject_locks")
    os.makedirs(lock_dir, exist_ok=True)
    safe = re.sub(r"[^A-Za-z0-9_.-]+", "_", f"{session}_{window}")
    path = os.path.join(lock_dir, f"{safe}.lock")
    fd = os.open(path, os.O_CREAT | os.O_RDWR, 0o600)
    try:
        import fcntl
        deadline = time.time() + timeout
        while True:
            try:
                fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
                break
            except BlockingIOError:
                if time.time() >= deadline:
                    raise TimeoutError("inject lock timeout")
                time.sleep(0.1)
        yield
    finally:
        try:
            import fcntl
            fcntl.flock(fd, fcntl.LOCK_UN)
        finally:
            os.close(fd)

def is_agent_idle(session, window, busy_markers=None):
    """
    反转策略：默认认为空闲，只有检测到明确的忙碌标记才返回 False。
    只检查 pane 最后 3 行（避免历史输出中的 spinner 残留干扰）。
    busy_markers=None 时使用内置 _BUSY_MARKERS (CC 默认值)。
    """
    content = capture_pane(session, window)
    if not content:
        return False  # 窗口不存在
    markers = busy_markers if busy_markers is not None else _BUSY_MARKERS
    # 只看最后 3 行，避免历史输出中的 spinner 残留
    last_lines = "\n".join(content.rstrip().split("\n")[-3:])
    for busy in markers:
        if busy in last_lines:
            return False
    return True  # 默认空闲

def send_ctrlc(session, window):
    """向 tmux 窗口发送 Ctrl+C，用于中断当前前台进程。"""
    subprocess.run(
        ["tmux", "send-keys", "-t", f"{session}:{window}", "C-c"],
        capture_output=True
    )

def _tail_text(text, max_len=240):
    """Normalize a prompt tail for best-effort submission checks."""
    return " ".join((text or "").split())[-max_len:]

def _press_submit(target):
    """Submit the current input line in a way that works across CLIs."""
    for key in ("Enter", "C-m"):
        r = subprocess.run(
            ["tmux", "send-keys", "-t", target, key],
            capture_output=True
        )
        if r.returncode != 0:
            return False
        time.sleep(0.2)
    return True

def _input_still_visible(session, window, text):
    """Best-effort check: did the prompt remain in the visible input area?"""
    if not text:
        return False
    needle = _tail_text(text)
    if not needle:
        return False
    residual = detect_unsubmitted_input_text(capture_pane(session, window))
    return needle in _tail_text(residual)

def check_agent_alive(session, window, stale_minutes=15):
    """检查 agent 是否存活。返回 (alive: bool, reason: str)"""
    # 1. 窗口存在性
    try:
        r = subprocess.run(
            ["tmux", "has-session", "-t", f"{session}:{window}"],
            capture_output=True, timeout=5
        )
        if r.returncode != 0:
            return False, "tmux窗口不存在"
    except Exception:
        return False, "tmux检测超时"

    # 2. 活跃度检测：capture pane，看是否有内容
    content = capture_pane(session, window)
    if not content.strip():
        return False, "窗口无输出内容"

    # 3. pane 活跃时间戳检测（tmux pane_activity 为 Unix 时间戳）
    try:
        r = subprocess.run(
            ["tmux", "display-message", "-t", f"{session}:{window}", "-p", "#{pane_activity}"],
            capture_output=True, text=True, timeout=5
        )
        if r.returncode == 0 and r.stdout.strip():
            last_activity = int(r.stdout.strip())
            idle_minutes = (time.time() - last_activity) / 60
            if idle_minutes > stale_minutes:
                return False, f"pane 已 {idle_minutes:.0f} 分钟无活动（阈值 {stale_minutes} 分钟）"
    except (ValueError, Exception):
        pass  # 无法获取时间戳时不阻塞，仅依赖前两项检测

    return True, "正常"

def inject_when_idle(session, window, text,
                     wait_secs=5, poll_interval=0.5, force_after_wait=True,
                     verify_submit=True):
    """
    等待 Agent 窗口空闲后注入文本（模拟用户输入并按 Enter）。

    参数:
      session         tmux session 名称
      window          tmux 窗口名称
      text            要注入的文本内容（可含 # $ 等特殊字符）
      wait_secs       最长等待空闲的秒数（默认 30s）
      poll_interval   轮询间隔（默认 2s）
      force_after_wait 超时后是否强制注入（默认 True）
      verify_submit  发送回车后复核输入是否仍停在输入框；若是则补一次提交

    返回:
      InjectionResult — 支持 bool(result) 兼容旧调用。

    实现细节:
      使用 send-keys -l（literal 模式），避免 # $ 等字符被 tmux 解释为变量。
      先发文本（-l），再单独发 Enter（非 literal，使其被识别为回车键）。
    """
    target = f"{session}:{window}"
    result = InjectionResult(ok=False, target=target)

    try:
        r = subprocess.run(
            ["tmux", "has-session", "-t", target],
            capture_output=True, timeout=5
        )
        if r.returncode != 0:
            result.error = "tmux target missing"
            return result
    except Exception as e:
        result.error = f"tmux target check failed: {e}"
        return result

    try:
        with _pane_inject_lock(session, window):
            before = capture_pane(session, window)
            result.tail_summary = _tail_summary("\n".join(before.splitlines()[-8:]))
            residual = detect_unsubmitted_input_text(before)
            if residual:
                result.unsafe_input = True
                result.error = "unsafe unsubmitted input"
                result.tail_summary = _tail_summary(residual)
                return result

            deadline = time.time() + wait_secs
            idle = False
            while time.time() < deadline:
                if is_agent_idle(session, window):
                    idle = True
                    break
                result.busy_before = True
                time.sleep(poll_interval)
            if not idle:
                result.busy_before = True
                if not force_after_wait:
                    result.error = "pane busy"
                    return result
                result.forced = True

            if len(text) > 600:
                result.method = "paste-buffer"
                tmp = tempfile.NamedTemporaryFile(mode="w", suffix=".txt",
                                                  delete=False, encoding="utf-8")
                tmp.write(text)
                tmp.close()
                buffer_name = f"_inject_{os.getpid()}_{int(time.time() * 1000)}"
                try:
                    r = subprocess.run(["tmux", "load-buffer", "-b", buffer_name, tmp.name],
                                       capture_output=True)
                    if r.returncode != 0:
                        result.error = "tmux load-buffer failed"
                        return result
                    r = subprocess.run(["tmux", "paste-buffer", "-b", buffer_name,
                                        "-d", "-t", target], capture_output=True)
                    if r.returncode != 0:
                        result.error = "tmux paste-buffer failed"
                        return result
                finally:
                    os.unlink(tmp.name)
            else:
                result.method = "send-keys"
                r = subprocess.run(
                    ["tmux", "send-keys", "-l", "-t", target, text],
                    capture_output=True
                )
                if r.returncode != 0:
                    result.error = "tmux send-keys failed"
                    return result

            time.sleep(0.5)
            if not _press_submit(target):
                result.error = "tmux submit failed"
                return result
            result.ok = True
            result.submitted = True
            if verify_submit:
                time.sleep(1.0)
                result.residual_visible = _input_still_visible(session, window, text)
                if result.residual_visible:
                    _press_submit(target)
                    time.sleep(0.5)
                    result.residual_visible = _input_still_visible(session, window, text)
                    if result.residual_visible:
                        result.submitted = False
                        result.error = "input residual visible after submit"
            return result
    except TimeoutError as e:
        result.error = str(e)
        return result
