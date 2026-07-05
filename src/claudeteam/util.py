"""Tiny shared helpers used by more than one command/module.

Keeping it small on purpose — anything bigger than a few one-liners
belongs in its own module under runtime/, store/, or feishu/.
"""
from __future__ import annotations

import contextlib
import json
import os
import subprocess
import sys
import time
from pathlib import Path

# File locking is platform-split: fcntl on POSIX (macOS / Linux), msvcrt
# region-locking on Windows. Imported conditionally so a bare
# `import claudeteam` doesn't die on Windows at import time.
if sys.platform == "win32":  # pragma: no cover — exercised on Windows CI
    import msvcrt
else:
    import fcntl


def usage_error(usage: str) -> int:
    """Print `usage` to stderr and return 1 — the standard \"bad args\"
    exit code. Use as `return usage_error(USAGE)` so the call-site
    reads as one statement instead of print-then-return."""
    print(usage, file=sys.stderr)
    return 1


def error_exit(msg: str, *, rc: int = 1) -> int:
    """Print `msg` to stderr and return `rc` (default 1).

    For \"something went wrong, exit non-zero\" sites that aren't a USAGE
    print — e.g. `return error_exit(f\"❌ unknown agent: {agent}\")`.
    """
    print(msg, file=sys.stderr)
    return rc


def warn(msg: str) -> None:
    """Print `msg` to stderr without exiting. For non-fatal issues where
    the caller wants to continue (`continue` in a loop, `rc |= 1` to
    flag, etc.). Pair with `error_exit` when the same site needs to bail."""
    print(msg, file=sys.stderr)


def help_requested(argv: list[str]) -> bool:
    """True if argv contains `-h` or `--help`. Used by every subcommand
    so they share one form (some used `argv[0] in (...)`, others
    `\"-h\" in argv or \"--help\" in argv` — same intent)."""
    return any(a in ("-h", "--help") for a in argv)


def maybe_print_help(argv: list[str], usage: str) -> bool:
    """If `argv` requested -h/--help, print `usage` to stdout and return True.
    Otherwise return False without printing.

    Lets a subcommand collapse the standard 3-line help-out pattern into
    one branch:

        def main(argv):
            if maybe_print_help(argv, USAGE):
                return 0
            ...

    Replaces the inline form (`if help_requested(argv): print(USAGE); return 0`)
    that appeared in 7+ commands.
    """
    if not help_requested(argv):
        return False
    print(usage)
    return True


def reject_extra_args(rest: list[str], usage: str) -> int | None:
    """If `rest` still holds positional args after pop_flag/pop_bool_flag
    consumed the recognised ones, print an `❌ unexpected args` error to
    stderr (with the offending tokens AND the usage line) and return 1.
    Otherwise return None so the caller continues.

    Centralises the four-site pattern:

        if rest:
            return error_exit(f\"❌ unexpected args: {rest}\\n{USAGE}\")

    Caller form:

        if (rc := reject_extra_args(rest, USAGE)) is not None:
            return rc
    """
    if not rest:
        return None
    return error_exit(f"❌ unexpected args: {rest}\n{usage}")


def reject_flag_as_agent(name: str, usage: str) -> int | None:
    """Guard for subcommands that take an agent NAME as a positional arg.

    A token starting with '-' is a misparsed option, never a real agent —
    the classic `claudeteam inbox --help`, where '--help' would otherwise be
    accepted as the agent and registered into facts (heartbeats / status),
    spawning a phantom '--help' agent that pollutes `/team`.
    Callers run `maybe_print_help` first so real
    -h/--help prints usage; this then rejects any *other* flag-shaped token.

    Prints a usage error to stderr and returns 1, else None so the caller
    continues. Caller form mirrors reject_extra_args:

        if (rc := reject_flag_as_agent(agent, USAGE)) is not None:
            return rc
    """
    if name.startswith("-"):
        return usage_error(
            f"❌ '{name}' 不是合法 agent 名（看起来是选项，不是 agent）\n{usage}")
    return None


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


def pop_bool_flag(rest: list[str], flag: str) -> bool:
    """Pop a boolean `flag` (no value) out of `rest`; return True iff present.
    Mutates `rest`. Pair with `pop_flag` for value-bearing flags.
    """
    if flag in rest:
        rest.remove(flag)
        return True
    return False


@contextlib.contextmanager
def flock(lock_path: Path):
    """Hold an exclusive file lock on `lock_path` for the body's lifetime.

    Creates the lock file (and parent dirs) on demand. Used by
    `store/local_facts.py` and `store/tasks.py` to serialize mutations
    to their JSON files. Single-host only — the semantics are
    process-local, not network-mounted.

    POSIX: blocking fcntl.flock. Windows: msvcrt.locking on the first
    byte — LK_LOCK gives up after ~10s, so we loop until acquired to
    keep the blocking contract identical across platforms.
    """
    lock_path.parent.mkdir(parents=True, exist_ok=True)
    with lock_path.open("a+") as fh:
        if sys.platform == "win32":  # pragma: no cover — Windows CI
            fh.seek(0)
            while True:
                try:
                    msvcrt.locking(fh.fileno(), msvcrt.LK_LOCK, 1)
                    break
                except OSError:
                    time.sleep(0.05)
            try:
                yield
            finally:
                fh.seek(0)
                try:
                    msvcrt.locking(fh.fileno(), msvcrt.LK_UNLCK, 1)
                except OSError:
                    pass
        else:
            fcntl.flock(fh.fileno(), fcntl.LOCK_EX)
            try:
                yield
            finally:
                fcntl.flock(fh.fileno(), fcntl.LOCK_UN)


def detached_popen_kwargs() -> dict:
    """Extra subprocess.Popen kwargs that put the child in its own
    session / process group, so (a) it can outlive the parent and (b)
    the parent can take the whole tree out in one call.

    POSIX: start_new_session=True (setsid). Windows: new process group +
    detached console — the closest equivalent (there is no setsid)."""
    if sys.platform == "win32":  # pragma: no cover — Windows CI
        return {"creationflags":
                (subprocess.CREATE_NEW_PROCESS_GROUP
                 | getattr(subprocess, "DETACHED_PROCESS", 0x00000008))}
    return {"start_new_session": True}


def terminate_process_group(proc, *, grace_s: float = 3.0) -> None:
    """Terminate `proc` AND its descendants, cross-platform.

    POSIX: SIGTERM the process group (child must have been spawned with
    `detached_popen_kwargs()`), escalating to SIGKILL after `grace_s`.
    Windows: `taskkill /T` takes the tree out (a bare terminate() would
    orphan grandchildren, e.g. the sidecar's node child)."""
    if proc.poll() is not None:
        return
    if sys.platform == "win32":  # pragma: no cover — Windows CI
        try:
            subprocess.run(["taskkill", "/F", "/T", "/PID", str(proc.pid)],
                           capture_output=True, timeout=10)
        except (OSError, subprocess.TimeoutExpired):
            try:
                proc.kill()
            except OSError:
                pass
        return
    import signal as _signal
    try:
        os.killpg(os.getpgid(proc.pid), _signal.SIGTERM)
    except (ProcessLookupError, PermissionError):
        return
    try:
        proc.wait(timeout=grace_s)
    except subprocess.TimeoutExpired:
        try:
            os.killpg(os.getpgid(proc.pid), _signal.SIGKILL)
        except (ProcessLookupError, PermissionError):
            pass


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


def read_jsonl(path: Path) -> list[dict]:
    """Read `path` as JSONL → list of records. Tolerant by design:

    - Missing file → [] (caller usually treats "no records" as the
      empty case, no need to special-case existence).
    - Blank lines → silently skipped.
    - Lines that fail json.loads → silently skipped (keeps the file
      forward-readable when a previous crash left a half-written line;
      callers can still write valid entries afterwards).

    Returns records oldest-first (file order), since JSONL is append-only.
    Callers that want newest-first reverse explicitly.
    """
    if not path.exists():
        return []
    rows: list[dict] = []
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            rows.append(json.loads(line))
        except json.JSONDecodeError:
            continue
    return rows


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

    Convention used everywhere in this project: `ensure_ascii=False` so
    Chinese strings stay readable in checked-in/audited files,
    `indent=2` for diff-friendliness, trailing newline so `cat` doesn't
    leave the prompt on the same line.
    """
    atomic_write_text(path, json.dumps(data, ensure_ascii=False, indent=2) + "\n")


def print_json(data) -> None:
    """Print `data` as pretty UTF-8 JSON to stdout — the convention
    every `--json` flag uses (`team`, `health`, `usage`).

    Same `ensure_ascii=False` + `indent=2` choices as `write_json` so
    machine-piped output (jq, grep, CI) gets identical
    formatting to checked-in JSON files. Single source of truth for
    these knobs — earlier the call was inlined in three commands and
    each could drift independently.
    """
    print(json.dumps(data, ensure_ascii=False, indent=2))


def env_str(name: str) -> str:
    """Return `os.environ[name].strip()` (empty str when unset). The strip
    handles `FOO=  bar  ` style sloppy quoting. Use `env_str(...) or
    \"<default>\"` for the canonical env-or-default str chain."""
    return os.environ.get(name, "").strip()


def env_path(name: str) -> Path | None:
    """Return `Path(env_str(name))` if non-empty, else None. Designed for
    the env-or-default-path pattern used by `paths.state_dir`,
    `config.team_file`, and `config.runtime_config_file`:

        return env_path(\"FOO_DIR\") or Path.cwd() / \"foo\"
    """
    val = env_str(name)
    return Path(val) if val else None


def now_ms() -> int:
    """Wall-clock time in epoch milliseconds (the project's canonical
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


def fmt_bytes(b: int) -> str:
    """Bytes → human-readable `2.34 GB / 56 MB / 7 KB / 42 B`.

    Promoted from per-module `_fmt_mem` mirrors (was duplicated in
    `runtime/server_metrics` and `feishu/slash` with explicit "Local
    mirror" disclaimer comments). Both /health text reporter + /health
    card builder format the same byte fields, so the helper genuinely
    has ≥ 2 call sites — earns its place in util per the two-use rule.
    """
    if b >= 1024**3:
        return f"{b / 1024**3:.2f} GB"
    if b >= 1024**2:
        return f"{b / 1024**2:.0f} MB"
    if b >= 1024:
        return f"{b / 1024:.0f} KB"
    return f"{b} B"


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
