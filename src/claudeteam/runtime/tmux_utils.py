"""tmux 工具函数 — 检测 Agent 空闲状态后安全注入文本

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
import subprocess, time, os, tempfile

# ── 状态特征字符串 ─────────────────────────────────────────────

# 出现这些 → 认为忙碌（正在处理，需要等待）
_BUSY_MARKERS = [
    "⣾", "⣽", "⣻", "⢿", "⡿", "⣟", "⣯", "⣷",  # braille spinner
    "◐", "◑", "◒", "◓",                            # arc spinner
    "Thinking", "Running tool",                      # Claude Code 状态文字
    # 注意：移除 "…" — 太常见，历史输出中大量出现，容易误判为忙碌
]

# ── 核心函数 ──────────────────────────────────────────────────

def capture_pane(session, window, lines: int | None = None):
    """
    获取 tmux pane 内容，返回字符串。lines 指定向上回溯行数（-S -N）。
    失败（窗口不存在等）返回空字符串。
    """
    try:
        cmd = ["tmux", "capture-pane", "-t", f"{session}:{window}", "-p"]
        if lines is not None:
            cmd += ["-S", f"-{lines}"]
        r = subprocess.run(cmd, capture_output=True, text=True, timeout=5)
        return r.stdout if r.returncode == 0 else ""
    except Exception:
        return ""

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
        subprocess.run(
            ["tmux", "send-keys", "-t", target, key],
            capture_output=True
        )
        time.sleep(0.2)

def _input_still_visible(session, window, text):
    """Best-effort check: did the prompt remain in the visible input area?"""
    if not text:
        return False
    needle = _tail_text(text)
    if not needle:
        return False
    pane = capture_pane(session, window)
    tail = _tail_text("\n".join(pane.rstrip().splitlines()[-12:]))
    return needle in tail

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
      True  — 注入成功（或强制注入）
      False — 目标窗口不存在

    实现细节:
      使用 send-keys -l（literal 模式），避免 # $ 等字符被 tmux 解释为变量。
      先发文本（-l），再单独发 Enter（非 literal，使其被识别为回车键）。
    """
    # 确认窗口存在
    try:
        r = subprocess.run(
            ["tmux", "has-session", "-t", f"{session}:{window}"],
            capture_output=True, timeout=5
        )
        if r.returncode != 0:
            return False
    except Exception:
        return False

    # 轮询等待空闲
    deadline = time.time() + wait_secs
    while time.time() < deadline:
        if is_agent_idle(session, window):
            break
        time.sleep(poll_interval)
    else:
        if not force_after_wait:
            return False
        # 超时后强制注入（允许打断）

    # 发送文本到 tmux
    target = f"{session}:{window}"
    if len(text) > 600:
        # 长文本：写临时文件 → tmux load-buffer → paste-buffer（绕过 pty 缓冲限制）
        tmp = tempfile.NamedTemporaryFile(mode="w", suffix=".txt",
                                          delete=False, encoding="utf-8")
        tmp.write(text)
        tmp.close()
        subprocess.run(["tmux", "load-buffer", "-b", "_inject", tmp.name],
                       capture_output=True)
        subprocess.run(["tmux", "paste-buffer", "-b", "_inject", "-d", "-t", target],
                       capture_output=True)
        os.unlink(tmp.name)
    else:
        # 短文本：用 -l 字面模式发送
        subprocess.run(
            ["tmux", "send-keys", "-l", "-t", target, text],
            capture_output=True
        )

    # 等待 TUI 处理完文本输入后再按 Enter。Codex/Kimi/Claude 的 TUI 对
    # paste-buffer 和 literal send-keys 的消化时机不同；Enter 后再补 C-m，
    # 并做一次可见区复核，避免消息堆在输入框里没有提交。
    time.sleep(0.5)
    _press_submit(target)
    if verify_submit:
        time.sleep(1.0)
        if _input_still_visible(session, window, text):
            _press_submit(target)

    return True
