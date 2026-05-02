"""`claudeteam init [--session NAME] [--force]`

First-time bootstrap for a new ClaudeTeam deployment.  Writes a starter
team.json + runtime_config.json next to the current working directory
(or wherever CLAUDETEAM_TEAM_FILE / CLAUDETEAM_RUNTIME_CONFIG point).

Refuses to overwrite existing files unless --force is passed.
"""
from __future__ import annotations


from claudeteam.runtime import config
from claudeteam.util import error_exit, help_requested, pop_flag, write_json


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


def main(argv: list[str]) -> int:
    rest = list(argv)
    if help_requested(rest):
        print(USAGE)
        return 0
    force = "--force" in rest
    if force:
        rest.remove("--force")
    session = pop_flag(rest, "--session") or _DEFAULT_TEAM["session"]
    if rest:
        return error_exit(f"❌ unexpected args: {rest}\n{USAGE}")

    team_path = config.team_file()
    rt_path = config.runtime_config_file()

    if not force:
        for path in (team_path, rt_path):
            if path.exists():
                return error_exit(f"❌ {path} already exists; pass --force to overwrite")

    team = dict(_DEFAULT_TEAM)
    team["session"] = session
    write_json(team_path, team)
    write_json(rt_path, _DEFAULT_RUNTIME)

    print(f"✅ wrote {team_path}")
    print(f"✅ wrote {rt_path}")
    print()
    print("Next:")
    print(f"  - edit {rt_path.name} to set chat_id + lark_profile (when wiring Feishu)")
    print(f"  - claudeteam up                   # tmux session '{session}' + router + watchdog")
    print(f"  - claudeteam health               # verify everything came up green")
    return 0
