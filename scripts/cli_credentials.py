#!/usr/bin/env python3
"""Compatibility wrapper for ClaudeTeam CLI credential diagnostics."""
from __future__ import annotations

import os
import sys

_SRC_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "src")
if _SRC_DIR not in sys.path:
    sys.path.insert(0, _SRC_DIR)

from claudeteam.runtime.cli_credentials import *  # noqa: F401,F403,E402
from claudeteam.runtime.cli_credentials import main  # noqa: E402


if __name__ == "__main__":
    raise SystemExit(main())
