"""`claudeteam switch <team-dir>` — print shell exports for a team directory.

Multi-team isolation today is env-var-based: a deployment is whichever
`team.json` + `runtime_config.json` + `CLAUDETEAM_STATE_DIR` the current
shell sees. Switching teams means re-exporting those three vars.

This command emits ready-to-eval export lines so the operator runs:

    eval "$(claudeteam switch ~/teams/projectA)"

The directory layout this assumes (created either by `claudeteam init`
in that dir or by hand) is:

    <team-dir>/
        team.json
        runtime_config.json
        state/                # auto-created when claudeteam writes anything

`team.json` is the marker file — switch refuses to point at a directory
without one, so a typo doesn't silently succeed.

With no argument, prints the current active team (resolved from env
vars) so an operator can confirm what they're pointing at without
greping their shell history.
"""
from __future__ import annotations

import shlex
from pathlib import Path

from claudeteam.runtime import config, paths
from claudeteam.util import env_str, error_exit, maybe_print_help


USAGE = (
    "usage: claudeteam switch [<team-dir>]\n"
    "  no arg          — print the current active team\n"
    "  <team-dir>      — print exports; wrap in `eval \"$(...)\"` to apply"
)


def _show_current() -> int:
    """Print the active team (resolved from env), one fact per line."""
    state = env_str("CLAUDETEAM_STATE_DIR") or f"(default) {paths.state_dir()}"
    team = env_str("CLAUDETEAM_TEAM_FILE") or f"(default) {config.team_file()}"
    rt = env_str("CLAUDETEAM_RUNTIME_CONFIG") or f"(default) {config.runtime_config_file()}"
    print(f"state_dir:      {state}")
    print(f"team_file:      {team}")
    print(f"runtime_config: {rt}")
    return 0


def _emit_exports(team_dir: Path) -> int:
    if not team_dir.exists():
        return error_exit(f"❌ {team_dir} does not exist")
    team_json = team_dir / "team.json"
    if not team_json.exists():
        return error_exit(
            f"❌ {team_json} not found — pass a directory containing team.json"
            f"\n   (run `claudeteam init` inside that directory first)")
    state_dir = team_dir / "state"
    rt_json = team_dir / "runtime_config.json"
    print(f"export CLAUDETEAM_STATE_DIR={shlex.quote(str(state_dir))}")
    print(f"export CLAUDETEAM_TEAM_FILE={shlex.quote(str(team_json))}")
    print(f"export CLAUDETEAM_RUNTIME_CONFIG={shlex.quote(str(rt_json))}")
    print(f"# Active team: {team_dir}")
    print(f"# Apply with: eval \"$(claudeteam switch {team_dir})\"")
    return 0


def main(argv: list[str]) -> int:
    rest = list(argv)
    if maybe_print_help(rest, USAGE):
        return 0
    if len(rest) > 1:
        return error_exit(f"❌ too many args: {rest}\n{USAGE}")
    if not rest:
        return _show_current()
    return _emit_exports(Path(rest[0]).expanduser().resolve())
