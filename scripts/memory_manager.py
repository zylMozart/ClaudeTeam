#!/usr/bin/env python3
"""Thin compat shell — delegates to src/claudeteam/commands/memory."""
import os as _os, sys as _sys
_SCRIPT_DIR = _os.path.dirname(_os.path.abspath(__file__))
_SRC_DIR = _os.path.join(_os.path.dirname(_SCRIPT_DIR), "src")
for _p in (_SCRIPT_DIR, _SRC_DIR):
    if _p not in _sys.path: _sys.path.insert(0, _p)
import claudeteam.commands.memory as _impl
_sys.modules[__name__] = _impl
if __name__ == "__main__":
    _impl.main()
