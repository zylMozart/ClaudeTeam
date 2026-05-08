#!/usr/bin/env python3
"""No-live smoke for the Codex launcher boundary."""
from __future__ import annotations

import os
import sys
import tempfile
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
SCRIPTS = ROOT / "scripts"
TESTS = ROOT / "tests"
SRC = ROOT / "src"
for path in (SCRIPTS, TESTS, SRC, ROOT):
    if str(path) not in sys.path:
        sys.path.insert(0, str(path))

from no_live_guard import install  # noqa: E402
from claudeteam.cli_adapters.codex_cli import CodexCliAdapter  # noqa: E402
from claudeteam.cli_adapters import adapter_for_agent  # noqa: E402


def test_codex_spawn_uses_read_only_preflight() -> None:
    cmd = CodexCliAdapter().spawn_cmd("manager", "gpt-5.4")
    assert "scripts/lib/run_codex_cli.sh manager" in cmd, cmd
    assert "--dangerously-bypass-approvals-and-sandbox" in cmd, cmd
    assert "--model gpt-5.4" in cmd, cmd
    assert "npm install" not in cmd
    assert "npx" not in cmd

    wrapper = (SCRIPTS / "lib" / "run_codex_cli.sh").read_text(encoding="utf-8")
    assert "npm install" not in wrapper
    assert "CLAUDETEAM_CODEX_REQUIRE_NPM_PACKAGE" in wrapper
    assert "exec codex" in wrapper


def test_adapter_for_agent_honors_temp_team_file() -> None:
    old_team_file = os.environ.get("CLAUDETEAM_TEAM_FILE")
    with tempfile.TemporaryDirectory() as tmp:
        team_file = Path(tmp) / "team.json"
        team_file.write_text(
            '{"session":"NoLive","agents":{"manager":{"cli":"codex-cli"}}}\n',
            encoding="utf-8",
        )
        os.environ["CLAUDETEAM_TEAM_FILE"] = str(team_file)
        try:
            assert adapter_for_agent("manager").process_name() == "codex"
        finally:
            if old_team_file is None:
                os.environ.pop("CLAUDETEAM_TEAM_FILE", None)
            else:
                os.environ["CLAUDETEAM_TEAM_FILE"] = old_team_file


def main() -> int:
    install()
    test_codex_spawn_uses_read_only_preflight()
    test_adapter_for_agent_honors_temp_team_file()
    print("OK: codex_launcher_smoke passed")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
