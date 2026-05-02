"""pytest fixtures shared by all suites.

Adds src/ to sys.path so test modules can `import claudeteam.X` without
installing the package, and asserts the rebuild stays no-live by default
(no real network / docker / tmux exec from inside tests).
"""
from __future__ import annotations

import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))
