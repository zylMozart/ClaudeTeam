"""`claudeteam version` — print the installed package version.

Reads version from the installed distribution metadata (set in
pyproject.toml `[project] version`). Useful in shell scripts:

    if [ "$(claudeteam version)" != "0.1.0" ]; then ...

and in smoke conductors that want to assert they're testing the
checkout they think they're testing.
"""
from __future__ import annotations

from claudeteam.util import maybe_print_help


def _read_version() -> str:
    """Read installed-distribution version with two fallbacks:

    1. `importlib.metadata.version("claudeteam")` — what `pip install -e .`
       and `pyproject.toml` set up.
    2. Hardcoded "0.0.0+unknown" — if the package somehow isn't on
       sys.path under that name (e.g. running directly from src/ in
       a fresh venv before `pip install -e`).
    """
    try:
        from importlib.metadata import version, PackageNotFoundError
        return version("claudeteam")
    except (PackageNotFoundError, ImportError):
        return "0.0.0+unknown"


def main(argv: list[str]) -> int:
    if maybe_print_help(argv, "usage: claudeteam version"):
        return 0
    print(_read_version())
    return 0
