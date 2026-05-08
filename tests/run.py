#!/usr/bin/env python3
"""Stdlib-only test runner.

Discovers `test_*` functions in `tests/unit/test_*.py` and runs them in
order.  Prints a one-line summary; exits non-zero on any failure.

We avoid pytest as a hard dep so contributors can run the gate from a
fresh virtualenv without installing anything.  When pytest is available
locally `python3 -m pytest` also works (pyproject.toml is configured).
"""
from __future__ import annotations

import importlib
import sys
import traceback
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "src"
TESTS = ROOT / "tests"
for _p in (SRC, TESTS):
    if str(_p) not in sys.path:
        sys.path.insert(0, str(_p))


def _discover(filt: str = "") -> list[tuple[str, str]]:
    """Return [(stem, sub), ...]. If `filt` given, keep modules whose
    stem contains the substring (so `python3 tests/run.py usage` runs
    only test_commands_usage / test_*usage*). Boss-flagged 2026-05-05:
    full suite is too slow during dev — let me filter."""
    out = []
    for sub in ("unit", "integration"):
        for path in sorted((ROOT / "tests" / sub).glob("test_*.py")):
            if filt and filt not in path.stem:
                continue
            out.append((path.stem, sub))
    return out


def _run_module(name: str, sub: str) -> tuple[int, int, list[str]]:
    mod = importlib.import_module(f"{sub}.{name}")
    passed = failed = 0
    failures: list[str] = []
    for attr in sorted(dir(mod)):
        if not attr.startswith("test_"):
            continue
        fn = getattr(mod, attr)
        if not callable(fn):
            continue
        try:
            fn()
            passed += 1
        except Exception:
            failed += 1
            failures.append(f"{sub}.{name}::{attr}\n{traceback.format_exc()}")
    return passed, failed, failures


def main() -> int:
    filt = sys.argv[1] if len(sys.argv) > 1 else ""
    total_pass = total_fail = 0
    all_failures: list[str] = []
    modules = _discover(filt)
    if filt and not modules:
        print(f"no test modules match filter {filt!r}")
        return 1
    for name, sub in modules:
        p, f, fails = _run_module(name, sub)
        marker = "OK " if f == 0 else "FAIL"
        suffix = f", {f} failed" if f else ""
        print(f"{marker} {sub}/{name}: {p} passed{suffix}")
        total_pass += p
        total_fail += f
        all_failures.extend(fails)

    if all_failures:
        print()
        for fail in all_failures:
            print(fail)

    print()
    print(f"tests: {total_pass} passed, {total_fail} failed")
    return 0 if total_fail == 0 else 1


if __name__ == "__main__":
    raise SystemExit(main())
