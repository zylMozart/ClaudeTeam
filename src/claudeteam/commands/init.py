"""`claudeteam init [--session NAME] [--force]`

First-time bootstrap for a new ClaudeTeam deployment.  Writes a starter
team.json + runtime_config.json next to the current working directory
(or wherever CLAUDETEAM_TEAM_FILE / CLAUDETEAM_RUNTIME_CONFIG point).

Refuses to overwrite existing files unless --force is passed.
"""
from __future__ import annotations

import json
import sys

from claudeteam.runtime import config
from claudeteam.util import atomic_write_text, pop_flag


USAGE = "usage: claudeteam init [--session NAME] [--force]"


_DEFAULT_TEAM = {
    "session": "ClaudeTeam",
    "agents": {
        "manager":      {"cli": "claude-code", "model": "opus",   "role": "团队主管"},
        "worker_cc":    {"cli": "claude-code", "model": "sonnet", "role": "Claude Code 员工"},
        "worker_codex": {"cli": "codex-cli",   "model": "gpt-5.5", "role": "Codex 员工"},
        "worker_kimi":  {"cli": "kimi-code",                     "role": "Kimi 员工"},
    },
    "default_model": "opus",
}


_DEFAULT_RUNTIME = {
    "chat_id": "",
    "lark_profile": "",
}


def _write_json(path, data: dict) -> None:
    atomic_write_text(path, json.dumps(data, ensure_ascii=False, indent=2) + "\n")


def main(argv: list[str]) -> int:
    rest = list(argv)
    if "-h" in rest or "--help" in rest:
        print(USAGE)
        return 0
    force = "--force" in rest
    if force:
        rest.remove("--force")
    session = pop_flag(rest, "--session") or _DEFAULT_TEAM["session"]
    if rest:
        print(f"❌ unexpected args: {rest}\n{USAGE}", file=sys.stderr)
        return 1

    team_path = config.team_file()
    rt_path = config.runtime_config_file()

    if team_path.exists() and not force:
        print(f"❌ {team_path} already exists; pass --force to overwrite", file=sys.stderr)
        return 1
    if rt_path.exists() and not force:
        print(f"❌ {rt_path} already exists; pass --force to overwrite", file=sys.stderr)
        return 1

    team = dict(_DEFAULT_TEAM)
    team["session"] = session
    _write_json(team_path, team)
    _write_json(rt_path, _DEFAULT_RUNTIME)

    print(f"✅ wrote {team_path}")
    print(f"✅ wrote {rt_path}")
    print()
    print("Next:")
    print(f"  - edit {rt_path.name} to set chat_id + lark_profile (when wiring Feishu)")
    print(f"  - claudeteam up                   # tmux session '{session}' + router + watchdog")
    print(f"  - claudeteam health               # verify everything came up green")
    return 0
