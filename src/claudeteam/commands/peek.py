"""`claudeteam peek <agent> [N]` — capture the last N lines of an agent's pane.

Local fast path equivalent to the `/tmux <agent> N` slash command but
without the chat round-trip. The manager identity calls out
`tmux capture-pane -t {session}:<agent> -p -S -30` for the 5-min
巡视 cadence; this command wraps it so:
- the session name comes from `team.json` instead of being hardcoded
- agent name validation gives a clear error vs tmux's silent empty
- N is clamped (default 30, max 2000 to match the slash version)

Output is plain text (the raw pane buffer) so it pipes cleanly to
grep / less / `claudeteam remember <agent> note "$(claudeteam peek X)"`.
"""
from __future__ import annotations

from claudeteam.runtime import config, tmux
from claudeteam.util import error_exit, maybe_print_help, usage_error


USAGE = "usage: claudeteam peek <agent> [N]   (default N=30, max 2000)"

_DEFAULT_LINES = 30
_MAX_LINES = 2000


def main(argv: list[str]) -> int:
    rest = list(argv)
    if maybe_print_help(rest, USAGE):
        return 0
    if len(rest) < 1:
        return usage_error(USAGE)
    agent = rest[0]
    raw_n = rest[1] if len(rest) >= 2 else ""
    if raw_n and not raw_n.isdigit():
        return error_exit(f"❌ N must be a positive integer (got {raw_n!r})")
    n = int(raw_n) if raw_n else _DEFAULT_LINES
    n = max(1, min(n, _MAX_LINES))

    try:
        config.agent_config(agent)
    except KeyError:
        return error_exit(f"❌ unknown agent: {agent} (not in team.json)")

    session = config.session_name()
    target = tmux.Target(session, agent)
    if not tmux.has_window(target):
        return error_exit(
            f"❌ {agent} has no pane in session {session} "
            f"(was it fired? try `claudeteam hire {agent}`)")

    buf = tmux.capture_pane(target, lines=n).rstrip()
    if not buf:
        print(f"(empty buffer for {agent})")
        return 0
    print(buf)
    return 0
