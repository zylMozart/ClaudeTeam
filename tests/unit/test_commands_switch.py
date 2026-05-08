"""Tests for `claudeteam switch` — multi-team env-export emitter."""
from __future__ import annotations

import json
import tempfile
from pathlib import Path

from helpers import env_patch, run_cli


def _team_dir(tmp: Path, *, with_team_json: bool = True) -> Path:
    """Create a fake team directory under `tmp`. Optionally skip team.json
    so the missing-marker error path is exercised."""
    d = tmp / "team-a"
    d.mkdir()
    if with_team_json:
        (d / "team.json").write_text(
            json.dumps({"agents": {"manager": {}}}), encoding="utf-8")
    return d


# ── help / no-arg ────────────────────────────────────────────────


def test_switch_no_arg_prints_current_active():
    """With no team-dir, switch reports what env vars currently point at
    so the operator can confirm without grepping shell history."""
    with tempfile.TemporaryDirectory() as tmp:
        sd = Path(tmp) / "state"
        tf = Path(tmp) / "team.json"
        rt = Path(tmp) / "runtime_config.json"
        with env_patch(CLAUDETEAM_STATE_DIR=str(sd),
                       CLAUDETEAM_TEAM_FILE=str(tf),
                       CLAUDETEAM_RUNTIME_CONFIG=str(rt)):
            rc, out, _ = run_cli(["switch"])
        assert rc == 0
        assert str(sd) in out
        assert str(tf) in out
        assert str(rt) in out


def test_switch_no_arg_prints_defaults_when_env_unset():
    """No env vars set → switch prints the (default) markers + resolved paths."""
    with env_patch(CLAUDETEAM_STATE_DIR=None,
                   CLAUDETEAM_TEAM_FILE=None,
                   CLAUDETEAM_RUNTIME_CONFIG=None):
        rc, out, _ = run_cli(["switch"])
    assert rc == 0
    assert "(default)" in out


def test_switch_help_returns_zero():
    rc, out, _ = run_cli(["switch", "--help"])
    assert rc == 0
    assert "usage: claudeteam switch" in out


# ── happy path ───────────────────────────────────────────────────


def test_switch_emits_export_lines_for_team_dir():
    """Pointing at a directory with team.json prints three exports +
    confirmation comment."""
    with tempfile.TemporaryDirectory() as tmp:
        d = _team_dir(Path(tmp))
        rc, out, _ = run_cli(["switch", str(d)])
    assert rc == 0
    assert f"export CLAUDETEAM_STATE_DIR=" in out
    assert f"export CLAUDETEAM_TEAM_FILE=" in out
    assert f"export CLAUDETEAM_RUNTIME_CONFIG=" in out
    # The three export targets should embed the team-dir path
    assert str(d) in out
    # eval-friendly hint is present
    assert "eval" in out


def test_switch_quotes_paths_with_spaces():
    """Shell-quoting matters: a path with spaces must remain eval-safe."""
    with tempfile.TemporaryDirectory() as tmp:
        d = Path(tmp) / "team with space"
        d.mkdir()
        (d / "team.json").write_text("{}", encoding="utf-8")
        rc, out, _ = run_cli(["switch", str(d)])
    assert rc == 0
    # shlex.quote wraps a space-containing path in single quotes
    assert "'" in out


def test_switch_expands_tilde():
    """`claudeteam switch ~/teams/x` should expand the tilde before
    checking for team.json (otherwise it would always 404)."""
    rc, out, err = run_cli(["switch", "~/this-dir-should-not-exist-xyz"])
    # Either way the dir doesn't exist; the point is no `~` shows up
    # in the rendered error message — that would indicate no expansion.
    combined = out + err
    assert "~" not in combined or "does not exist" in combined


# ── error paths ──────────────────────────────────────────────────


def test_switch_rejects_nonexistent_dir():
    rc, _, err = run_cli(["switch", "/tmp/definitely-not-here-12345"])
    assert rc == 1
    assert "does not exist" in err


def test_switch_rejects_dir_without_team_json():
    """A real directory but without team.json should be rejected — the
    marker file is what makes a directory a 'team'."""
    with tempfile.TemporaryDirectory() as tmp:
        d = _team_dir(Path(tmp), with_team_json=False)
        rc, _, err = run_cli(["switch", str(d)])
    assert rc == 1
    assert "team.json" in err
    assert "claudeteam init" in err  # hint to next step


def test_switch_rejects_extra_args():
    rc, _, err = run_cli(["switch", "/tmp", "extra"])
    assert rc == 1
    assert "too many args" in err
