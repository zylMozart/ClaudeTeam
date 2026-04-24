#!/usr/bin/env python3
"""Thin compat shell — delegates to src/claudeteam/commands/tmux.

`import tmux_command` or `from tmux_command import X` transparently hits
the src module.
"""
import os as _os
import sys as _sys

_SCRIPT_DIR = _os.path.dirname(_os.path.abspath(__file__))
if _SCRIPT_DIR not in _sys.path:
    _sys.path.insert(0, _SCRIPT_DIR)
_SRC_DIR = _os.path.join(_os.path.dirname(_SCRIPT_DIR), "src")
if _SRC_DIR not in _sys.path:
    _sys.path.insert(0, _SRC_DIR)

import claudeteam.commands.tmux as _impl  # noqa: E402

_sys.modules[__name__] = _impl
