"""Tests for `claudeteam install-hooks` — Claude Code slash-command markdowns."""
from __future__ import annotations

import os
import tempfile
from pathlib import Path

from helpers import run_cli


# ── happy path ──────────────────────────────────────────────────


def test_install_hooks_creates_md_per_command():
    with tempfile.TemporaryDirectory() as tmp:
        rc, out, _ = run_cli(["install-hooks", tmp])
        assert rc == 0

        cmds_dir = Path(tmp) / ".claude" / "commands"
        assert cmds_dir.exists()
        # at least the documented commands
        for name in ("inbox", "team", "status", "say", "task", "health"):
            assert (cmds_dir / f"{name}.md").exists(), f"missing {name}.md"
        assert "wrote 6 slash command" in out


def test_install_hooks_idempotent_overwrites_existing_files():
    with tempfile.TemporaryDirectory() as tmp:
        run_cli(["install-hooks", tmp])
        # tweak one to test overwrite
        team_path = Path(tmp) / ".claude" / "commands" / "team.md"
        team_path.write_text("STALE", encoding="utf-8")

        rc, out, _ = run_cli(["install-hooks", tmp])
        assert rc == 0
        assert "overwritten" in out
        assert "STALE" not in team_path.read_text(encoding="utf-8")


def test_install_hooks_default_target_is_cwd():
    with tempfile.TemporaryDirectory() as tmp:
        cwd = os.getcwd()
        os.chdir(tmp)
        try:
            rc, _, _ = run_cli(["install-hooks"])
            assert rc == 0
            assert (Path(tmp) / ".claude" / "commands" / "team.md").exists()
        finally:
            os.chdir(cwd)


def test_install_hooks_say_md_mentions_chat():
    with tempfile.TemporaryDirectory() as tmp:
        run_cli(["install-hooks", tmp])
        say_md = (Path(tmp) / ".claude" / "commands" / "say.md").read_text(encoding="utf-8")
        assert "Feishu chat" in say_md
        assert "claudeteam say" in say_md


# ── parsing ──────────────────────────────────────────────────────


def test_install_hooks_too_many_args_returns_one():
    rc, _, err = run_cli(["install-hooks", "/a", "/b"])
    assert rc == 1
    assert "usage:" in err


def test_install_hooks_help():
    rc, out, _ = run_cli(["install-hooks", "--help"])
    assert rc == 0
    assert "usage: claudeteam install-hooks" in out
