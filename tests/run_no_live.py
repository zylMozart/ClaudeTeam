#!/usr/bin/env python3
"""Run ClaudeTeam's default offline test suite.

This is the default test entry point for structure/refactor work.  It installs
a no-live guard before running tests so the suite cannot touch real Feishu,
tmux, Docker, network, or credential-backed tools.
"""
from __future__ import annotations

import runpy
import sys
import traceback
from pathlib import Path

from no_live_guard import NoLiveAccessError, install


ROOT = Path(__file__).resolve().parents[1]
SCRIPTS = ROOT / "scripts"
if str(SCRIPTS) not in sys.path:
    sys.path.insert(0, str(SCRIPTS))
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))


TESTS = (
    ("static skill layout", ROOT / "tests" / "static_skill_layout_check.py"),
    ("static public contracts", ROOT / "tests" / "static_public_contract_check.py"),
    ("no bitable core smoke", ROOT / "tests" / "no_bitable_core_smoke.py"),
    ("message rendering regression", ROOT / "scripts" / "regression_message_rendering.py"),
    ("message sanitizer regression", ROOT / "scripts" / "regression_message_sanitizer.py"),
    ("local facts regression", ROOT / "scripts" / "regression_local_facts.py"),
)


def run_one(label: str, path: Path) -> bool:
    print(f"== {label} ==")
    try:
        runpy.run_path(str(path), run_name="__main__")
    except SystemExit as exc:
        code = exc.code if isinstance(exc.code, int) else 1
        if code == 0:
            print(f"PASS: {label}\n")
            return True
        print(f"FAIL: {label} exited {code}\n")
        return False
    except NoLiveAccessError as exc:
        print(f"FAIL: {label} attempted live access: {exc}\n")
        return False
    except Exception:
        traceback.print_exc()
        print(f"FAIL: {label}\n")
        return False
    print(f"PASS: {label}\n")
    return True


def main() -> int:
    install()
    passed = 0
    for label, path in TESTS:
        if run_one(label, path):
            passed += 1
    total = len(TESTS)
    print(f"no-live tests: {passed}/{total} passed")
    return 0 if passed == total else 1


if __name__ == "__main__":
    raise SystemExit(main())
