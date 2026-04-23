#!/usr/bin/env python3
"""Compatibility wrapper for the default no-live test suite."""
from __future__ import annotations

import runpy
import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


if __name__ == "__main__":
    tests_dir = str(ROOT / "tests")
    if tests_dir not in sys.path:
        sys.path.insert(0, tests_dir)
    runpy.run_path(str(ROOT / "tests" / "run_no_live.py"), run_name="__main__")
