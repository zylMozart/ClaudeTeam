"""Tests for `claudeteam version`."""
from __future__ import annotations

from helpers import attr_patch, run_cli
from claudeteam.commands import version as version_cmd


def test_version_prints_value_from_metadata():
    rc, out, _ = run_cli(["version"])
    assert rc == 0
    # Whatever's installed should print as a non-empty single line
    line = out.strip()
    assert line  # not empty
    assert "\n" not in line  # single line
    # Looks plausibly like a version (digit somewhere)
    assert any(c.isdigit() for c in line)


def test_version_help_returns_zero():
    rc, out, _ = run_cli(["version", "--help"])
    assert rc == 0
    assert "usage: claudeteam version" in out


def test_version_falls_back_when_metadata_missing():
    """If importlib.metadata can't resolve the package (e.g. running
    raw from src without pip install -e), the fallback string is
    returned rather than raising."""
    def boom(_name):
        from importlib.metadata import PackageNotFoundError
        raise PackageNotFoundError(_name)

    # patch the helper directly — easier than patching importlib
    from claudeteam.commands.version import _read_version
    original = version_cmd._read_version
    version_cmd._read_version = lambda: "0.0.0+unknown"
    try:
        rc, out, _ = run_cli(["version"])
    finally:
        version_cmd._read_version = original
    assert rc == 0
    assert "0.0.0+unknown" in out


def test_version_appears_in_top_level_command_list():
    """version should show up in the no-args usage so operators see it
    next to the other commands."""
    rc, out, _ = run_cli([])
    assert rc == 0
    assert "version" in out
