"""Single source of truth for runtime filesystem paths.

All paths derive from `$CLAUDETEAM_STATE_DIR` (re-read on every call so
tests get isolation by setting the env, not by monkey-patching).  When
not set, falls back to a `state/` dir beside the config file — the config's
location is the team's identity, so two teams never share one global dir.

Layout:
    $CLAUDETEAM_STATE_DIR/
        facts/             ← inbox.json, status.json, logs.jsonl, heartbeats.json
        share/             ← team-shared experience (experience.jsonl)
        agents/<name>/     ← one dir per agent:
                               identity.md, memory.jsonl
                               workspace/  ← private scratch / long reports
                               home/       ← the CLI's HOME (.claude / .codex / ...)
        router.pid         ← daemon pid files
        watchdog.pid
        router.cursor      ← catchup replay state
"""
from __future__ import annotations

from pathlib import Path

from claudeteam.util import env_path, env_str


def state_dir() -> Path:
    """Top-level directory for all runtime state.

    Defaults to a `state/` dir beside the config file (so each team dir keeps
    its own state and two teams can't bleed into one shared global dir).
    Override with $CLAUDETEAM_STATE_DIR — e.g. a Docker volume."""
    return env_path("CLAUDETEAM_STATE_DIR") or config_file().parent / "state"


def facts_dir() -> Path:
    """Where local_facts stores inbox / status / log / heartbeats."""
    return state_dir() / "facts"


def agent_dir(agent: str) -> Path:
    """Root of one agent's own space.

    Holds `identity.md` + `memory.jsonl`, the `workspace/` scratch dir, and
    the `home/` subdir (the CLI's HOME, where claude looks for ~/.claude —
    see `agent_home` below). CLI-agnostic: the same location
    for every agent regardless of which CLI it runs under. The native
    CLAUDE.md under `home/` is a projection of the identity/memory kept
    here."""
    return state_dir() / "agents" / agent


def agent_workspace(agent: str) -> Path:
    """Per-agent private scratch area (drafts, long reports, temp files).

    Agents collaborate in the shared project repo (the pane's cwd); this
    is the one directory each agent owns, so long content / scratch doesn't
    collide across panes in the repo root."""
    return agent_dir(agent) / "workspace"


def agent_home(agent: str) -> str:
    """Per-agent HOME — the `home/` subdir of the agent's own state dir.

    Returns a str (it gets spliced into shell spawn commands). Defaults to
    `<state_dir>/agents/<agent>/home`, so each agent's CLI dotfiles
    (`.claude` / `.codex` / `.gemini` / ...) sit beside its `identity.md` +
    `memory.jsonl` — one directory per agent. Set `CLAUDETEAM_AGENT_HOME_ROOT`
    to relocate the homes onto a separate mount (e.g. a Docker volume that
    persists creds across image rebuilds, or a writable path on macOS where
    `~` is a read-only firmlink); the home is then `<root>/<agent>`.
    """
    root = env_str("CLAUDETEAM_AGENT_HOME_ROOT")
    if root:
        return str(Path(root) / agent)
    return str(agent_dir(agent) / "home")


def acp_agent_dir(agent: str) -> Path:
    """Per-agent ACP runner state: queue.json (delivery state machine),
    session.json (sessionId for resume), transcript.log (streamed output,
    what the viewer pane tails), agent.pid (host-spawned subprocess)."""
    return state_dir() / "acp" / agent


def share_dir() -> Path:
    """Team-shared knowledge space: durable experience the whole team reads
    and writes (distinct from `facts/`, which is live coordination state)."""
    return state_dir() / "share"


def state_file(name: str) -> Path:
    """A file under state_dir. Caller is responsible for mkdir before writing
    — pure path resolution, no I/O side effects."""
    return state_dir() / name


def router_pid_file() -> Path:
    return state_file("router.pid")


def router_cursor_file() -> Path:
    return state_file("router.cursor")


def router_log_file() -> Path:
    return state_file("router.log")


def router_seen_file() -> Path:
    return state_file("router.seen")


def config_file() -> Path:
    """Path to the unified TOML config file (replaces team.json +
    runtime_config.json). Override via CLAUDETEAM_CONFIG_FILE env, else
    looks for `./claudeteam.toml` relative to cwd."""
    from claudeteam.util import env_path
    return env_path("CLAUDETEAM_CONFIG_FILE") or Path.cwd() / "claudeteam.toml"


def watchdog_pid_file() -> Path:
    return state_file("watchdog.pid")


def watchdog_log_file() -> Path:
    return state_file("watchdog.log")


def ensure_state_dir() -> Path:
    """Create state_dir if missing and return it. Use when about to write."""
    sd = state_dir()
    sd.mkdir(parents=True, exist_ok=True)
    return sd
