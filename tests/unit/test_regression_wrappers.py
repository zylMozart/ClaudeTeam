from __future__ import annotations

import contextlib
import io
from types import SimpleNamespace

import pytest


@contextlib.contextmanager
def quiet_stdout():
    buf = io.StringIO()
    with contextlib.redirect_stdout(buf):
        yield buf


@pytest.mark.unit
@pytest.mark.regression
def test_regression_boss_todo_main_runs_without_live_calls():
    import regression_boss_todo

    with quiet_stdout():
        regression_boss_todo.main()


@pytest.mark.unit
@pytest.mark.regression
def test_regression_tmux_inject_main_runs_without_live_calls():
    import regression_tmux_inject

    with quiet_stdout():
        regression_tmux_inject.main()


@pytest.mark.unit
@pytest.mark.regression
def test_regression_message_sanitizer_main_runs_without_live_calls():
    import regression_message_sanitizer

    with quiet_stdout():
        regression_message_sanitizer.main()


@pytest.mark.unit
@pytest.mark.regression
def test_message_rendering_shell_argv_regression_uses_mocked_subprocess(monkeypatch):
    import regression_message_rendering

    def fake_run(cmd, **kwargs):
        script = cmd[-1] if isinstance(cmd, list) and cmd else ""
        if "mktemp" in script:
            return SimpleNamespace(returncode=0, stdout="'Next line\\n'", stderr="")
        return SimpleNamespace(returncode=0, stdout="'Codex command:\\\\nNext line'", stderr="")

    monkeypatch.setattr(regression_message_rendering.subprocess, "run", fake_run)
    regression_message_rendering.check_shell_argv_regression()


@pytest.mark.unit
@pytest.mark.regression
def test_regression_message_rendering_main_runs_without_live_calls(monkeypatch):
    import regression_message_rendering

    monkeypatch.setattr(
        regression_message_rendering,
        "check_shell_argv_regression",
        lambda: None,
    )

    with quiet_stdout():
        regression_message_rendering.main()
