"""`claudeteam usage` — token / credit consumption snapshot.

Today's coverage is honest:
  - claude-code agents → shells out to `npx ccusage <view>` (community
    tool that reads ~/.claude/projects logs) when available
  - codex / kimi      → no upstream usage tool; we say so

Pure shell-out wrapper, no caching. Add new CLI types here as their
ecosystems grow tools.

Useful when the boss asks "how much did this team burn today?" or
when planning lazy-wake configuration. With `--json`, dump a
machine-readable record so smoke conductors / dashboards can ingest
the same numbers without re-parsing the formatted output.
"""
from __future__ import annotations

import json
import shutil
import subprocess
from typing import Callable

from claudeteam.runtime import config
from claudeteam.util import error_exit, help_requested, pop_bool_flag, pop_flag


USAGE = ("usage: claudeteam usage [--view daily|monthly|session|blocks] "
         "[--days N] [--json]")

# ccusage's documented views — validated against argv for clearer errors
_VIEWS = ("daily", "monthly", "session", "blocks")


def _run_ccusage(view: str, days: str = "",
                 *, runner: Callable | None = None) -> tuple[int, str]:
    """Invoke ccusage via npx and return (rc, combined_output)."""
    if runner is None:
        runner = lambda argv: subprocess.run(argv, capture_output=True, text=True, timeout=60)
    if shutil.which("npx") is None:
        return 1, "(npx not on PATH; install Node.js to use ccusage)"
    argv = ["npx", "-y", "ccusage", view]
    if days:
        argv += ["--days", days]
    try:
        r = runner(argv)
    except subprocess.TimeoutExpired:
        return 1, "(ccusage timed out after 60s)"
    except OSError as e:
        return 1, f"(ccusage exec failed: {e})"
    out = (r.stdout or "") + (r.stderr or "")
    return r.returncode, out


_NO_TOOL = "no upstream usage tool — track via the provider dashboard"
_UNKNOWN = "unknown — no usage adapter"
_KNOWN_NO_TOOL = ("codex-cli", "kimi-code", "kimi-cli")


def _note_for(cli: str) -> str:
    return _NO_TOOL if cli in _KNOWN_NO_TOOL else _UNKNOWN


def _build_data(view: str, days: str, clis: set[str]) -> dict:
    """Run ccusage if applicable, return a structured record. Used by
    both the text renderer (formatted lines) and the --json renderer."""
    data = {
        "view": view,
        "days": days or None,
        "clis": sorted(clis),
        "claude_code": None,
        "other_clis": [],
    }
    if "claude-code" in clis:
        rc, out = _run_ccusage(view, days)
        data["claude_code"] = {
            "rc": rc,
            "ok": rc == 0,
            "output": out,
            "lines": (out or "").splitlines(),
        }
    for cli in sorted(clis):
        if cli == "claude-code":
            continue
        data["other_clis"].append({"cli": cli, "note": _note_for(cli)})
    return data


def _emit_text(data: dict) -> None:
    print(f"━━ usage ({data['view']}) ━━")
    cc = data.get("claude_code")
    if cc is not None:
        print("\nclaude-code (via ccusage):")
        if not cc["ok"]:
            print("  ⚠️  ccusage failed:")
            for line in cc["lines"]:
                print(f"    {line}")
        else:
            for line in cc["lines"]:
                print(f"  {line}")
    if data["other_clis"]:
        print("\nother CLIs:")
        for row in data["other_clis"]:
            print(f"  {row['cli']}: {row['note']}")


def _emit_json(data: dict) -> None:
    print(json.dumps(data, ensure_ascii=False, indent=2))


def main(argv: list[str]) -> int:
    rest = list(argv)
    if help_requested(rest):
        print(USAGE)
        return 0

    as_json = pop_bool_flag(rest, "--json")
    view = pop_flag(rest, "--view") or "daily"
    days = pop_flag(rest, "--days") or ""
    if rest:
        return error_exit(f"❌ unexpected args: {rest}\n{USAGE}")
    if view not in _VIEWS:
        return error_exit(f"❌ unknown view: {view} (valid: {' / '.join(_VIEWS)})")

    try:
        agents = config.load_team().get("agents", {})
        clis = {a.get("cli", "claude-code") for a in agents.values()}
    except Exception:
        clis = set()

    data = _build_data(view, days, clis)
    if as_json:
        _emit_json(data)
    else:
        _emit_text(data)
    return 0
