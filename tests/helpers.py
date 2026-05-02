"""Shared test fixtures.

Every test file that touches local_facts or runtime config used to
roll its own `_isolated_state()` / `_isolated_team()` context manager
+ `_run()` helper (~15 lines each, 10 files ≈ 150 LOC of boilerplate).
Centralised here.

Usage:
    from helpers import isolated_env, run_cli

    with isolated_env() as tmp:
        rc, out, err = run_cli(["send", "a", "b", "msg"])

    with isolated_env(team={"agents": {"a": {"cli": "claude-code"}}}):
        ...

    with isolated_env(team={...}, runtime_config={"chat_id": "oc_x"}):
        ...
"""
from __future__ import annotations

import contextlib
import io
import json
import os
import tempfile
from pathlib import Path


_ENV_KEYS = ("CLAUDETEAM_TEAM_FILE", "CLAUDETEAM_RUNTIME_CONFIG", "CLAUDETEAM_STATE_DIR")


@contextlib.contextmanager
def isolated_env(*, team: dict | None = None, runtime_config: dict | None = None):
    """Set CLAUDETEAM_STATE_DIR (always) + optionally TEAM_FILE / RUNTIME_CONFIG.

    Yields the tempdir Path.  All env changes are reverted on exit.
    """
    with tempfile.TemporaryDirectory() as tmp:
        tmp_path = Path(tmp)
        old = {k: os.environ.get(k) for k in _ENV_KEYS}
        os.environ["CLAUDETEAM_STATE_DIR"] = str(tmp_path / "state")
        # Always isolate team/runtime paths so files in $PWD don't leak in.
        team_path = tmp_path / "team.json"
        rt_path = tmp_path / "runtime_config.json"
        os.environ["CLAUDETEAM_TEAM_FILE"] = str(team_path)
        os.environ["CLAUDETEAM_RUNTIME_CONFIG"] = str(rt_path)
        if team is not None:
            team_path.write_text(json.dumps(team, ensure_ascii=False), encoding="utf-8")
        if runtime_config is not None:
            rt_path.write_text(json.dumps(runtime_config, ensure_ascii=False), encoding="utf-8")
        try:
            yield tmp_path
        finally:
            for key, val in old.items():
                if val is None:
                    os.environ.pop(key, None)
                else:
                    os.environ[key] = val


def run_cli(argv: list[str]) -> tuple[int, str, str]:
    """Invoke `cli.main(argv)`, capture stdout/stderr, return (rc, out, err)."""
    from claudeteam import cli
    out, err = io.StringIO(), io.StringIO()
    with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
        rc = cli.main(argv)
    return rc, out.getvalue(), err.getvalue()
