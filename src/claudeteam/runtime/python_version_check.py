"""Python interpreter version enforcement for ClaudeTeam entry points.

Single source of truth for the runtime check that mirrors
``pyproject.toml`` ``requires-python = ">=3.10"``. Invoked from:

  • ``scripts/setup.py``         (right after ``sys.path`` bootstrap, before any
                                   other ``claudeteam.*`` import).
  • ``scripts/start-team.sh``    (right after ``PYTHONPATH=src`` export, before
                                   any heavy work).
  • ``scripts/docker-entrypoint.sh`` (same position).

This module must remain importable on Python 3.7+ — it is the *first* thing the
above entry points load, before any 3.10+ syntax can crash the import. Don't
add walrus operators, ``match`` statements, ``X | Y`` type unions, or any
``from __future__`` imports that break older interpreters here.
"""
import sys

REQUIRED = (3, 10)


def require_py310():
    """Hard-fail if the current interpreter is older than Python 3.10.

    On success: returns ``None`` and the entry point continues. On failure:
    writes a multi-line upgrade-path message to stderr and calls
    ``sys.exit(1)`` — entry points that invoke this don't need to handle the
    return value.

    Kept dependency-free (only ``sys``) so it can run before any optional
    third-party packages are importable.
    """
    if sys.version_info >= REQUIRED:
        return
    cur = "{}.{}.{}".format(
        sys.version_info.major, sys.version_info.minor, sys.version_info.micro
    )
    msg = (
        "❌ ClaudeTeam 需要 Python 3.10+，当前: "
        "{cur} ({exe})\n"
        "   pyproject.toml 声明 requires-python>=3.10，"
        "请升级后再跑。\n"
        "   推荐升级路径:\n"
        "     • macOS:   brew install python@3.11   "
        "# 然后用 python3.11 跑入口\n"
        "     • Linux:   apt/yum 装 python3.11，"
        "或用 pyenv install 3.11 / uv python install 3.11\n"
        "     • 通用:    pyenv install 3.11.* "
        "&& pyenv local 3.11.*\n"
        "   注: PYTHONPATH=src 只是开发期 fallback，"
        "不能用作 3.9 的版本兼容补丁。\n"
    ).format(cur=cur, exe=sys.executable)
    sys.stderr.write(msg)
    sys.exit(1)
