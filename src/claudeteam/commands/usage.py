"""`claudeteam usage` — token / credit consumption snapshot.

Today's coverage is honest:
  - claude-code agents → shells out to `npx ccusage <view>` (community
    tool that reads ~/.claude/projects logs) when available
  - codex / kimi      → no upstream usage tool; we say so

Pure shell-out wrapper, no caching. Add new CLI types here as their
ecosystems grow tools.

Useful when the boss asks "how much did this team burn today?" or
when planning lazy-wake configuration.
"""
from __future__ import annotations

import shutil
import subprocess
import sys
from typing import Callable

from claudeteam.runtime import config
from claudeteam.util import pop_flag


USAGE = "usage: claudeteam usage [--view daily|monthly|session|blocks] [--days N]"


def _run_ccusage(view: str, *, runner: Callable | None = None) -> tuple[int, str]:
    """Invoke ccusage via npx and return (rc, combined_output)."""
    if runner is None:
        runner = lambda argv: subprocess.run(argv, capture_output=True, text=True, timeout=60)
    if shutil.which("npx") is None:
        return 1, "(npx not on PATH; install Node.js to use ccusage)"
    r = runner(["npx", "-y", "ccusage", view])
    out = (r.stdout or "") + (r.stderr or "")
    return r.returncode, out


def _summary_for_clis(clis: set[str]) -> list[str]:
    lines = []
    for cli in sorted(clis):
        if cli == "claude-code":
            continue   # handled by ccusage block
        if cli in ("codex-cli", "kimi-code", "kimi-cli"):
            lines.append(f"  {cli}: no upstream usage tool — track via the provider dashboard")
        else:
            lines.append(f"  {cli}: unknown — no usage adapter")
    return lines


def main(argv: list[str]) -> int:
    rest = list(argv)
    if "-h" in rest or "--help" in rest:
        print(USAGE)
        return 0

    view = pop_flag(rest, "--view") or "daily"
    days = pop_flag(rest, "--days") or ""
    if rest:
        print(f"❌ unexpected args: {rest}\n{USAGE}", file=sys.stderr)
        return 1
    if view not in {"daily", "monthly", "session", "blocks"}:
        print(f"❌ unknown view: {view}", file=sys.stderr)
        return 1

    try:
        clis = {config.agent_cli(a) for a in config.agent_names()}
    except Exception:
        clis = set()

    print(f"━━ usage ({view}) ━━")

    if "claude-code" in clis:
        print("\nclaude-code (via ccusage):")
        view_arg = view if not days else f"{view} --days {days}"
        rc, out = _run_ccusage(view_arg)
        if rc != 0:
            print("  ⚠️  ccusage failed:")
            for line in (out or "").splitlines():
                print(f"    {line}")
        else:
            for line in out.splitlines():
                print(f"  {line}")

    other = _summary_for_clis(clis)
    if other:
        print("\nother CLIs:")
        for line in other:
            print(line)

    return 0
