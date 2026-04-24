#!/usr/bin/env python3
"""Thin compat shell — delegates to src/claudeteam/runtime/config.

`import config` or `from config import X` transparently hits the src module.
CLI entry preserved: python3 scripts/config.py {resolve-model|resolve-thinking} <agent>
"""
import os as _os
import sys as _sys

_SCRIPT_DIR = _os.path.dirname(_os.path.abspath(__file__))
if _SCRIPT_DIR not in _sys.path:
    _sys.path.insert(0, _SCRIPT_DIR)
_SRC_DIR = _os.path.join(_os.path.dirname(_SCRIPT_DIR), "src")
if _SRC_DIR not in _sys.path:
    _sys.path.insert(0, _SRC_DIR)

import claudeteam.runtime.config as _impl

# Redirect module-level attribute lookups to src so monkey-patching and
# `import config; config.X` always hit the canonical impl.
_sys.modules[__name__] = _impl

if __name__ == "__main__":
    _argv = _sys.argv[1:]
    if len(_argv) == 2 and _argv[0] == "resolve-model":
        try:
            print(_impl.resolve_model_for_agent(_argv[1]))
        except _impl.InvalidModelError as _e:
            print(f"❌ {_e}", file=_sys.stderr)
            _sys.exit(1)
        except Exception as _e:
            print(f"❌ 解析 {_argv[1]} 模型失败: {_e}", file=_sys.stderr)
            _sys.exit(1)
    elif len(_argv) == 2 and _argv[0] == "resolve-thinking":
        try:
            print(_impl.resolve_thinking_for_agent(_argv[1]))
        except _impl.InvalidThinkingError as _e:
            print(f"❌ {_e}", file=_sys.stderr)
            _sys.exit(1)
        except Exception as _e:
            print(f"❌ 解析 {_argv[1]} thinking 失败: {_e}", file=_sys.stderr)
            _sys.exit(1)
    else:
        print("用法: python3 scripts/config.py {resolve-model|resolve-thinking} <agent_name>",
              file=_sys.stderr)
        _sys.exit(2)
