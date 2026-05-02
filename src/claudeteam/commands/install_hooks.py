"""`claudeteam install-hooks` — drop slash-command markdowns for Claude Code agents.

Writes `.claude/commands/{name}.md` files at cwd so any Claude Code
pane spawned in this directory gets `/inbox`, `/team`, `/status`,
`/say`, `/task` slash commands wired to the corresponding `claudeteam`
subcommand.

Each markdown instructs the agent to first read its own identity.md
(written by `agents/identity.py` on hire/start) so it knows which
agent it is, then run the appropriate command. We can't bake the
agent name into the file because all panes share one .claude/
directory.

Idempotent — overwrites existing files. Codex and Kimi panes ignore
.claude/ so this is harmless for them.
"""
from __future__ import annotations

import sys
from pathlib import Path

from claudeteam.util import help_requested, usage_error


USAGE = "usage: claudeteam install-hooks [path]   (default: $PWD)"


_HEAD = """\
You are a ClaudeTeam agent. Read $CLAUDETEAM_STATE_DIR/agents/<your-name>/identity.md
to confirm your name (or check `tmux display-message -p '#W'` if env is unset);
your name comes from the tmux window you're running in.

"""


_COMMANDS: dict[str, str] = {
    "inbox": _HEAD + (
        "Run `claudeteam inbox <your-name>` to list unread messages. "
        "Acknowledge each with `claudeteam read <local_id>` once you start work on it.\n"
    ),
    "team": _HEAD + (
        "Run `claudeteam team` to see every agent's status + heartbeat. "
        "Use this before delegating to confirm targets are alive.\n"
    ),
    "status": _HEAD + (
        "Run `claudeteam status <your-name> <state> <task>` where state is one of "
        "`进行中 / 已完成 / 阻塞 / 待命`. Update at every meaningful transition.\n"
    ),
    "say": _HEAD + (
        "Take the user's argument as the message to post in the Feishu chat as you. "
        "Run `claudeteam say <your-name> \"<message>\"`.\n"
    ),
    "task": _HEAD + (
        "Manage the task tracker:\n"
        "- `claudeteam task list` to see open work\n"
        "- `claudeteam task create <assignee> <title>` to add\n"
        "- `claudeteam task done <T-id>` when finished\n"
    ),
    "health": (
        "Run `claudeteam health` and summarize: any red checks? any agent with "
        "no heartbeat in the last 30 minutes?\n"
    ),
}


def _write_command_files(target_dir: Path) -> tuple[int, int]:
    """Write each slash-command .md. Returns (created, overwritten)."""
    target_dir.mkdir(parents=True, exist_ok=True)
    created = 0
    overwritten = 0
    for name, body in _COMMANDS.items():
        path = target_dir / f"{name}.md"
        if path.exists():
            overwritten += 1
        else:
            created += 1
        path.write_text(body, encoding="utf-8")
    return created, overwritten


def main(argv: list[str]) -> int:
    rest = list(argv)
    if help_requested(rest):
        print(USAGE)
        return 0
    if len(rest) > 1:
        return usage_error(USAGE)

    base = Path(rest[0]) if rest else Path.cwd()
    target = base / ".claude" / "commands"
    created, overwritten = _write_command_files(target)
    total = created + overwritten
    print(f"✅ wrote {total} slash command(s) to {target}")
    if overwritten:
        print(f"   ({overwritten} overwritten, {created} new)")
    print("\nClaude Code panes spawned in this directory now respond to:")
    for name in sorted(_COMMANDS):
        print(f"  /{name}")
    return 0
