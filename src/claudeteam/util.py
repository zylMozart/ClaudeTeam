"""Tiny shared helpers used by more than one command/module.

Keeping it small on purpose — anything bigger than a few one-liners
belongs in its own module under runtime/, store/, or feishu/.
"""
from __future__ import annotations

import contextlib
import fcntl
import json
import os
import sys
import time
from pathlib import Path


def usage_error(usage: str) -> int:
    """Print `usage` to stderr and return 1 — the standard \"bad args\"
    exit code. Use as `return usage_error(USAGE)` so the call-site
    reads as one statement instead of print-then-return."""
    print(usage, file=sys.stderr)
    return 1


def error_exit(msg: str, *, rc: int = 1) -> int:
    """Print `msg` to stderr and return `rc` (default 1).

    For \"something went wrong, exit non-zero\" sites that aren't a USAGE
    print — e.g. \`return error_exit(f\"❌ unknown agent: {agent}\")\`.
    """
    print(msg, file=sys.stderr)
    return rc


def warn(msg: str) -> None:
    """Print `msg` to stderr without exiting. For non-fatal issues where
    the caller wants to continue (\`continue\` in a loop, \`rc |= 1\` to
    flag, etc.). Pair with `error_exit` when the same site needs to bail."""
    print(msg, file=sys.stderr)


def help_requested(argv: list[str]) -> bool:
    """True if argv contains \`-h\` or \`--help\`. Used by every subcommand
    so they share one form (some used \`argv[0] in (...)\`, others
    \`\"-h\" in argv or \"--help\" in argv\` — same intent)."""
    return any(a in ("-h", "--help") for a in argv)


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


def read_json(path: Path, default):
    """Read `path` as JSON, or return `default` if the file is missing.

    Lets the JSONDecodeError propagate on corrupt files — callers that
    want fault-tolerance wrap explicitly. Used by config / store /
    catchup / etc. so each can express \"missing-is-the-default-value\"
    in one line.
    """
    if not path.exists():
        return default
    return json.loads(path.read_text(encoding="utf-8"))


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


def write_json(path: Path, data) -> None:
    """Atomically write `data` as pretty-printed UTF-8 JSON.

    Convention used everywhere in the rebuild: `ensure_ascii=False` so
    Chinese strings stay readable in checked-in/audited files,
    `indent=2` for diff-friendliness, trailing newline so `cat` doesn't
    leave the prompt on the same line.
    """
    atomic_write_text(path, json.dumps(data, ensure_ascii=False, indent=2) + "\n")


def env_path(name: str) -> Path | None:
    """Return `Path(os.environ[name].strip())` if the variable is set to a
    non-empty value, else None. Designed for the env-or-default-path
    pattern used by `paths.state_dir`, `config.team_file`, and
    `config.runtime_config_file`:

        return env_path(\"FOO_DIR\") or Path.cwd() / \"foo\"
    """
    val = os.environ.get(name, "").strip()
    return Path(val) if val else None


def now_ms() -> int:
    """Wall-clock time in epoch milliseconds (the rebuild's canonical
    timestamp resolution). Local stores all serialize this directly."""
    return int(time.time() * 1000)


def fmt_time_ms(ms: int, *, fmt: str = "%m-%d %H:%M") -> str:
    """Format an epoch-ms timestamp as local time. Returns `?` for falsy
    inputs (uninitialized rows). Default `%m-%d %H:%M` matches inbox /
    task listings; pass `fmt="%m-%d %H:%M:%S"` for log lines.
    """
    if not ms:
        return "?"
    return time.strftime(fmt, time.localtime(ms / 1000))


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
