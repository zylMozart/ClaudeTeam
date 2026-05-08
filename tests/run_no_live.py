#!/usr/bin/env python3
"""Run ClaudeTeam's default offline test suite.

This is the default test entry point for structure/refactor work.  It installs
a no-live guard before running tests so the suite cannot touch real Feishu,
tmux, Docker, network, or credential-backed tools.
"""
from __future__ import annotations

import runpy
import os
import sys
import tempfile
import traceback
from pathlib import Path

from no_live_guard import NoLiveAccessError, install


ROOT = Path(__file__).resolve().parents[1]
SCRIPTS = ROOT / "scripts"
if str(SCRIPTS) not in sys.path:
    sys.path.insert(0, str(SCRIPTS))
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

FORBIDDEN_RUNTIME_PATHS = (
    "workspace",
    "state",
    "agents",
    "scripts/runtime_config.json",
    "team.json",
)


TESTS = (
    ("static skill layout", ROOT / "tests" / "static_skill_layout_check.py"),
    ("static public contracts", ROOT / "tests" / "static_public_contract_check.py"),
    ("compat import paths", ROOT / "tests" / "compat_import_paths.py"),
    ("compat scripts entrypoints", ROOT / "tests" / "compat_scripts_entrypoints.py"),
    ("no bitable core smoke", ROOT / "tests" / "no_bitable_core_smoke.py"),
    ("runtime state paths smoke", ROOT / "tests" / "runtime_state_paths_smoke.py"),
    ("watchdog state machine gate", ROOT / "tests" / "watchdog_state_machine_gate.py"),
    ("codex launcher smoke", ROOT / "tests" / "codex_launcher_smoke.py"),
    ("message rendering regression", ROOT / "tests" / "regression_message_rendering.py"),
    ("message sanitizer regression", ROOT / "tests" / "regression_message_sanitizer.py"),
    ("feishu client send unit", ROOT / "tests" / "test_feishu_client_send.py"),
    ("feishu msg say unit", ROOT / "tests" / "test_feishu_msg_say.py"),
    ("local facts regression", ROOT / "tests" / "regression_local_facts.py"),
    ("router dispatch unit", ROOT / "tests" / "test_router_dispatch.py"),
    ("router cursor unit", ROOT / "tests" / "test_router_cursor.py"),
    ("router daemon runtime unit", ROOT / "tests" / "test_router_daemon_runtime.py"),
    ("router wake unit", ROOT / "tests" / "test_router_wake.py"),
    ("tmux wake readiness unit", ROOT / "tests" / "test_tmux_wake_ready.py"),
    ("slash dispatch unit", ROOT / "tests" / "test_slash_dispatch.py"),
    ("slash handlers unit", ROOT / "tests" / "test_slash_handlers.py"),
)


def run_one(label: str, path: Path) -> bool:
    print(f"== {label} ==")
    try:
        runpy.run_path(str(path), run_name="__main__")
    except SystemExit as exc:
        code = exc.code if isinstance(exc.code, int) else 1
        if code == 0:
            print(f"PASS: {label}\n")
            return True
        print(f"FAIL: {label} exited {code}\n")
        return False
    except NoLiveAccessError as exc:
        print(f"FAIL: {label} attempted live access: {exc}\n")
        return False
    except Exception:
        traceback.print_exc()
        print(f"FAIL: {label}\n")
        return False
    print(f"PASS: {label}\n")
    return True


def _exists_safe(path: Path) -> bool:
    try:
        return path.exists()
    except PermissionError:
        # If unreadable, treat as existing baseline and skip creation checks.
        return True


def _file_sig(path: Path):
    try:
        st = path.stat()
    except (FileNotFoundError, PermissionError):
        return None
    return (st.st_mtime_ns, st.st_size)


def main() -> int:
    install()
    baseline_exists = {
        rel: _exists_safe(ROOT / rel) for rel in FORBIDDEN_RUNTIME_PATHS
    }
    baseline_file_sigs = {
        rel: _file_sig(ROOT / rel)
        for rel in ("scripts/runtime_config.json", "team.json")
    }

    old_state_dir = os.environ.get("CLAUDETEAM_STATE_DIR")
    old_team_file = os.environ.get("CLAUDETEAM_TEAM_FILE")
    old_pending_dir = os.environ.get("CLAUDETEAM_PENDING_DIR")
    result_code = 1
    with tempfile.TemporaryDirectory() as tmp:
        tmp_path = Path(tmp)
        team_file = tmp_path / "team.json"
        team_file.write_text(
            """{"session":"ClaudeTeamNoLive","agents":{"manager":{},"devops":{},"security":{},"toolsmith":{},"researcher":{},"qa_smoke":{},"docs_keeper":{},"architect":{},"coder":{}}}\n""",
            encoding="utf-8",
        )
        os.environ["CLAUDETEAM_STATE_DIR"] = str(tmp_path / "state")
        os.environ["CLAUDETEAM_TEAM_FILE"] = str(team_file)
        os.environ["CLAUDETEAM_PENDING_DIR"] = str(
            tmp_path / "state" / "queue" / "pending_msgs"
        )
        passed = 0
        for label, path in TESTS:
            if run_one(label, path):
                passed += 1
        total = len(TESTS)
        print(f"no-live tests: {passed}/{total} passed")
        result_code = 0 if passed == total else 1

        pollution = []
        for rel, existed_before in baseline_exists.items():
            if existed_before:
                continue
            if _exists_safe(ROOT / rel):
                pollution.append(rel)
        if pollution:
            print(
                "FAIL: no-live runtime pollution created repo paths: "
                + ", ".join(sorted(pollution))
            )
            result_code = 1

        file_mutations = []
        for rel, sig_before in baseline_file_sigs.items():
            if sig_before is None:
                continue
            sig_after = _file_sig(ROOT / rel)
            if sig_after is not None and sig_after != sig_before:
                file_mutations.append(rel)
        if file_mutations:
            print(
                "FAIL: no-live mutated repo runtime file(s): "
                + ", ".join(sorted(file_mutations))
            )
            result_code = 1

    if old_state_dir is None:
        os.environ.pop("CLAUDETEAM_STATE_DIR", None)
    else:
        os.environ["CLAUDETEAM_STATE_DIR"] = old_state_dir
    if old_team_file is None:
        os.environ.pop("CLAUDETEAM_TEAM_FILE", None)
    else:
        os.environ["CLAUDETEAM_TEAM_FILE"] = old_team_file
    if old_pending_dir is None:
        os.environ.pop("CLAUDETEAM_PENDING_DIR", None)
    else:
        os.environ["CLAUDETEAM_PENDING_DIR"] = old_pending_dir
    return result_code


if __name__ == "__main__":
    raise SystemExit(main())
