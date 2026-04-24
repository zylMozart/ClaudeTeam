#!/usr/bin/env python3
"""No-live smoke for router/watchdog runtime state paths."""
from __future__ import annotations

import importlib
import os
import sys
import tempfile
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
SCRIPTS = ROOT / "scripts"
TESTS = ROOT / "tests"
for path in (SCRIPTS, TESTS, ROOT):
    if str(path) not in sys.path:
        sys.path.insert(0, str(path))

from no_live_guard import install  # noqa: E402


ENV_KEYS = (
    "CLAUDETEAM_STATE_DIR",
    "CLAUDETEAM_TEAM_FILE",
    "CLAUDETEAM_ENABLE_FEISHU_REMOTE",
    "CLAUDETEAM_ENABLE_BITABLE_LEGACY",
)


def _reload(name: str):
    if name in sys.modules:
        return importlib.reload(sys.modules[name])
    return importlib.import_module(name)


def _assert_under(path: str, root: Path) -> None:
    resolved = Path(path).resolve()
    assert resolved == root or root in resolved.parents, f"{path} is not under {root}"


def _assert_not_under(path: str, root: Path) -> None:
    resolved = Path(path).resolve()
    assert resolved != root and root not in resolved.parents, f"{path} is under {root}"


def test_router_and_watchdog_runtime_paths() -> None:
    original_env = {key: os.environ.get(key) for key in ENV_KEYS}
    original_modules = {
        name: sys.modules.get(name)
        for name in ("config", "claudeteam.runtime.paths", "feishu_router", "watchdog")
    }
    with tempfile.TemporaryDirectory() as tmp:
        tmp_path = Path(tmp)
        state_dir = tmp_path / "state"
        team_file = tmp_path / "team.json"
        team_file.write_text(
            '{"session":"ClaudeTeamNoLive","agents":{"manager":{},"devops":{}}}\n',
            encoding="utf-8",
        )
        os.environ["CLAUDETEAM_STATE_DIR"] = str(state_dir)
        os.environ["CLAUDETEAM_TEAM_FILE"] = str(team_file)
        os.environ["CLAUDETEAM_ENABLE_FEISHU_REMOTE"] = "1"
        os.environ["CLAUDETEAM_ENABLE_BITABLE_LEGACY"] = "1"
        try:
            _reload("claudeteam.runtime.paths")
            router = _reload("feishu_router")
            watchdog = _reload("watchdog")

            for path in (
                router.PID_FILE,
                router.CURSOR_FILE,
                router.TMUX_INTERCEPT_LOG,
                router.ROUTER_MSG_DIR,
                watchdog.ROUTER_PID_FILE,
                watchdog.ROUTER_CURSOR_FILE,
                watchdog.KANBAN_PID_FILE,
                watchdog.WATCHDOG_PID_FILE,
                watchdog._PID_FILE,
            ):
                _assert_under(path, state_dir.resolve())
                _assert_not_under(path, SCRIPTS.resolve())

            assert Path(router.LEGACY_PID_FILE).resolve() == SCRIPTS / ".router.pid"
            assert Path(router.LEGACY_CURSOR_FILE).resolve() == SCRIPTS / ".router.cursor"
            assert Path(watchdog.LEGACY_ROUTER_PID_FILE).resolve() == SCRIPTS / ".router.pid"

            legacy_cursor_file = router.LEGACY_CURSOR_FILE
            legacy_pid_file = router.LEGACY_PID_FILE
            try:
                router.LEGACY_CURSOR_FILE = str(state_dir / "missing-legacy-router.cursor")
                router.LEGACY_PID_FILE = str(state_dir / "missing-legacy-router.pid")
                router._advance_cursor_to(1234.0)
                assert Path(router.CURSOR_FILE).read_text() == "1234.000"
                assert router._load_cursor() == 1234.0

                router.acquire_pid_lock()
                assert Path(router.PID_FILE).read_text() == str(os.getpid())
                router._cleanup_pid()
                assert not Path(router.PID_FILE).exists()
            finally:
                router.LEGACY_CURSOR_FILE = legacy_cursor_file
                router.LEGACY_PID_FILE = legacy_pid_file

            watchdog._acquire_pid_lock()
            assert Path(watchdog._PID_FILE).read_text() == str(os.getpid())
            watchdog._cleanup_pid()
            assert not Path(watchdog._PID_FILE).exists()
        finally:
            for key, value in original_env.items():
                if value is None:
                    os.environ.pop(key, None)
                else:
                    os.environ[key] = value
            for name, module in original_modules.items():
                if module is None:
                    sys.modules.pop(name, None)
                else:
                    sys.modules[name] = module


def main() -> int:
    install()
    test_router_and_watchdog_runtime_paths()
    print("OK: runtime_state_paths_smoke passed")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
