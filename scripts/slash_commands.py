#!/usr/bin/env python3
"""Compatibility wrapper for ClaudeTeam slash commands."""
from __future__ import annotations

import os
import sys
from pathlib import Path

_SRC_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "src")
if _SRC_DIR not in sys.path:
    sys.path.insert(0, _SRC_DIR)

from claudeteam.commands.slash.standalone import dispatch  # noqa: E402,F401


if __name__ == "__main__":
    text = " ".join(sys.argv[1:])
    matched, reply = dispatch(text)
    if matched and reply is not None:
        print(reply)
    raise SystemExit(0 if matched else 1)
