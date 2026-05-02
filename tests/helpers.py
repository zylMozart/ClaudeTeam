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
from dataclasses import dataclass
from pathlib import Path


@dataclass
class FakeProc:
    """Stand-in for `subprocess.CompletedProcess` in test_*. Use as the
    return value from a fake `run` callable to drive
    `runtime.tmux` / `feishu.lark` test paths."""
    returncode: int = 0
    stdout: str = ""
    stderr: str = ""


class CallRecorder:
    """Stub callable that records each (args, kwargs) invocation and
    returns a scripted result. Used to verify what arguments a wrapper
    handed to a subprocess / lark / etc.

        rec = CallRecorder({"message_id": "om_1"})
        out = chat.send_text(..., lark_run=rec)
        assert "--chat-id" in rec.calls[0]["args"]
    """

    def __init__(self, result=None):
        self.calls: list[dict] = []
        self.result = result

    def __call__(self, args, **kwargs):
        self.calls.append({"args": list(args), "kwargs": dict(kwargs)})
        return self.result


@contextlib.contextmanager
def isolated_env(*, team: dict | None = None, runtime_config: dict | None = None):
    """Set CLAUDETEAM_STATE_DIR (always) + optionally TEAM_FILE / RUNTIME_CONFIG.

    Yields the tempdir Path.  All env changes are reverted on exit.
    """
    with tempfile.TemporaryDirectory() as tmp:
        tmp_path = Path(tmp)
        team_path = tmp_path / "team.json"
        rt_path = tmp_path / "runtime_config.json"
        if team is not None:
            team_path.write_text(json.dumps(team, ensure_ascii=False), encoding="utf-8")
        if runtime_config is not None:
            rt_path.write_text(json.dumps(runtime_config, ensure_ascii=False), encoding="utf-8")
        with env_patch(
            CLAUDETEAM_STATE_DIR=str(tmp_path / "state"),
            CLAUDETEAM_TEAM_FILE=str(team_path),
            CLAUDETEAM_RUNTIME_CONFIG=str(rt_path),
        ):
            yield tmp_path


def run_cli(argv: list[str]) -> tuple[int, str, str]:
    """Invoke `cli.main(argv)`, capture stdout/stderr, return (rc, out, err)."""
    from claudeteam import cli
    out, err = io.StringIO(), io.StringIO()
    with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
        rc = cli.main(argv)
    return rc, out.getvalue(), err.getvalue()


@contextlib.contextmanager
def env_patch(**kvs):
    """Temporarily set os.environ vars; pass `val=None` to delete the var
    for the duration. Originals are saved and restored on exit, even if
    the test raises.

        with env_patch(FOO_DIR=tmp, BAR=None):
            ...

    Sister to `attr_patch` — same save/swap/restore pattern, applied to
    process env vars instead of module attributes.
    """
    old = {k: os.environ.get(k) for k in kvs}
    for k, v in kvs.items():
        if v is None:
            os.environ.pop(k, None)
        else:
            os.environ[k] = str(v)
    try:
        yield
    finally:
        for k, v in old.items():
            if v is None:
                os.environ.pop(k, None)
            else:
                os.environ[k] = v


@contextlib.contextmanager
def attr_patch(module, **stubs):
    """Temporarily replace named attributes on `module` with the given
    callables (or any value). Restored on exit, even if the test raises.

        with attr_patch(some_module, helper=fake): ...

    Use for one-off mocking when there's no module-specific helper
    (`tmux_patch` wraps this for the most common case).
    """
    saved = {name: getattr(module, name) for name in stubs}
    for name, value in stubs.items():
        setattr(module, name, value)
    try:
        yield
    finally:
        for name, value in saved.items():
            setattr(module, name, value)


def tmux_patch(**stubs):
    """Temporarily replace one or more functions on `claudeteam.runtime.tmux`.

    Sugar over `attr_patch` for the common case — see attr_patch for the
    general form.

        with tmux_patch(has_session=lambda s: False, kill_session=lambda s: True):
            ...
    """
    from claudeteam.runtime import tmux as _tmux
    return attr_patch(_tmux, **stubs)
