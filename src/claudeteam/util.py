"""Tiny shared helpers used by more than one command/module.

Keeping it small on purpose — anything bigger than a few one-liners
belongs in its own module under runtime/, store/, or feishu/.
"""
from __future__ import annotations

import contextlib
import fcntl
import time
from pathlib import Path


def pop_flag(rest: list[str], flag: str) -> str | None:
    """Pop `flag <value>` out of `rest` and return value; or None if absent
    or value is missing. Mutates `rest`. Used by every command that does its
    own argv parsing (init, task, usage, workspace, ...).
    """
    if flag not in rest:
        return None
    i = rest.index(flag)
    if i + 1 >= len(rest):
        return None
    val = rest[i + 1]
    del rest[i:i + 2]
    return val


@contextlib.contextmanager
def flock(lock_path: Path):
    """Hold an exclusive fcntl lock on `lock_path` for the body's lifetime.

    Creates the lock file (and parent dirs) on demand. Used by
    `store/local_facts.py` and `store/tasks.py` to serialize mutations
    to their JSON files. Single-host only — fcntl semantics are
    process-local, not network-mounted.
    """
    lock_path.parent.mkdir(parents=True, exist_ok=True)
    with lock_path.open("a+") as fh:
        fcntl.flock(fh.fileno(), fcntl.LOCK_EX)
        try:
            yield
        finally:
            fcntl.flock(fh.fileno(), fcntl.LOCK_UN)


def atomic_write_text(path: Path, content: str, *, encoding: str = "utf-8") -> None:
    """Write `content` to `path` via tmp + rename so a crash mid-write
    can't leave the destination half-written.

    Creates parent directories if missing. Idempotent on retry: a leftover
    tmp from a previous crash gets clobbered next time.
    """
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(content, encoding=encoding)
    tmp.replace(path)


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
