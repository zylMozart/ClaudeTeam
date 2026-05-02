"""Tiny shared helpers used by more than one command/module.

Keeping it small on purpose — anything bigger than a few one-liners
belongs in its own module under runtime/, store/, or feishu/.
"""
from __future__ import annotations

import time


def ago_ms(ms: int, *, now: float | None = None) -> str:
    """Format a millisecond epoch timestamp as `Ns ago / Nm ago / Nh ago / Nd ago`.

    Returns `?` when ms is 0 or falsy. `now` is injectable for tests.
    """
    if not ms:
        return "?"
    current = now if now is not None else time.time()
    secs = max(0, int(current - ms / 1000))
    if secs < 60:
        return f"{secs}s ago"
    if secs < 3600:
        return f"{secs // 60}m ago"
    if secs < 86400:
        return f"{secs // 3600}h ago"
    return f"{secs // 86400}d ago"
