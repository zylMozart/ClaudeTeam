"""Live agent state classification from a tmux pane capture buffer.

`/team` and friends use this to surface what each agent is *actually*
doing right now (not just whatever they upserted into status.json).
The classifier is content-aware: looks at trailing prompts, working
spinners, quota warnings, compacting markers, etc.

Lifted from the old branch's `commands/slash/team.py:parse_state_fallback`
— same emoji/brief vocabulary so operators see consistent state across
old and new deployments.
"""
from __future__ import annotations

import re


_BASH_PROMPT_RE = re.compile(r"root@[0-9a-f]+:[^#]*#\s*$")
_PERM_PROMPT_RE = re.compile(r"❯\s*\d\.")
_BYPASS_RE = re.compile(r"⏵⏵\s*bypass permissions")
_WORK_TIME_RE = re.compile(r"\((\d+m\s*\d+s|\d+s)(?:\s*·[^)]*)?\)")
# Codex idle: status line shows "gpt-5.5 default · ~/path" or
# "permissions: YOLO" inside the boxed banner.
_CODEX_IDLE_RE = re.compile(r"\b(?:gpt-\d|o1|o3|o4|codex)\S*\s+default\b")
# Kimi idle: ready markers from adapter — "context:" line or "── input"
_KIMI_IDLE_RE = re.compile(r"context:\s*[\d.]+%|── input|Send /help for help")


def parse(buf: str) -> tuple[str, str]:
    """Classify a tmux pane capture into (emoji, brief).

    Returns the same vocabulary as the old branch:
      ⬜ no window / empty buffer
      🛑 CLI not running (back to bash)
      ⛔ quota exceeded
      ⚠️ awaiting permission prompt
      🗜️ compacting context
      🔄 working / thinking (with elapsed time when available)
      💤 idle (CLI ready, no active task)
      🔘 unknown — show last non-empty line tail
    """
    if not buf:
        return ("⬜", "no window")
    low = buf.lower()
    tail_lines = [line for line in buf.splitlines() if line.strip()]
    tail = tail_lines[-1] if tail_lines else ""

    if _BASH_PROMPT_RE.search(tail):
        return ("🛑", "CLI not running (bash)")
    if "hit your limit" in low:
        return ("⛔", "quota exceeded")
    if "do you want to proceed" in low or _PERM_PROMPT_RE.search(buf):
        return ("⚠️", "awaiting permission")
    if "compacting conversation" in low or "compacting…" in low:
        return ("🗜️", "compacting")
    if "esc to interrupt" in low:
        m = _WORK_TIME_RE.search(buf)
        return ("🔄", f"working {m.group(1) if m else ''}".strip())
    if "manifesting" in low:
        return ("🔄", "thinking")
    if _BYPASS_RE.search(buf) or "new task?" in low:
        return ("💤", "idle")
    if "permissions: yolo" in low or _CODEX_IDLE_RE.search(buf):
        return ("💤", "idle")
    if _KIMI_IDLE_RE.search(buf):
        return ("💤", "idle")
    return ("🔘", tail.strip()[:40])
