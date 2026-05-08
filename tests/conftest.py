"""pytest fixtures shared by all suites.

Adds src/ and tests/ to sys.path so test modules can `import claudeteam.X`
and `from helpers import isolated_env` without installing the package.
"""
from __future__ import annotations

import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "src"
TESTS = ROOT / "tests"
for p in (SRC, TESTS):
    if str(p) not in sys.path:
        sys.path.insert(0, str(p))
