"""Team and runtime configuration.

Two files:
  team.json       — static team layout (which agents, which CLI, which model)
  runtime_config.json — per-deployment runtime values (chat_id, lark profile)

Both paths come from env so tests get isolation by setting CLAUDETEAM_TEAM_FILE
and CLAUDETEAM_RUNTIME_CONFIG.

Schema (team.json):
    {
      "session": "ClaudeTeam",
      "agents": {
        "manager":      {"cli": "claude-code", "model": "opus", "role": "..."},
        "worker_cc":    {"cli": "claude-code", "model": "sonnet"},
        "worker_codex": {"cli": "codex-cli",   "model": "gpt-5.5"},
        "worker_kimi":  {"cli": "kimi-code"}
      },
      "default_model": "opus"
    }

Reading is no-cache (re-read on every call) so editing team.json picks up
without restart.  Writes are explicit via save_runtime_config().
"""
from __future__ import annotations

import os
from pathlib import Path

from claudeteam.util import env_path, read_json, write_json


# ── path resolution ───────────────────────────────────────────────


def team_file() -> Path:
    return env_path("CLAUDETEAM_TEAM_FILE") or Path.cwd() / "team.json"


def runtime_config_file() -> Path:
    return env_path("CLAUDETEAM_RUNTIME_CONFIG") or Path.cwd() / "runtime_config.json"


# ── team.json ────────────────────────────────────────────────────


_DEFAULT_TEAM: dict = {"session": "ClaudeTeam", "agents": {}, "default_model": "opus"}


def load_team() -> dict:
    return read_json(team_file(), dict(_DEFAULT_TEAM))


def session_name() -> str:
    return load_team().get("session", "ClaudeTeam")


def agent_names() -> list[str]:
    return sorted(load_team().get("agents", {}))


def agent_config(agent: str) -> dict:
    """Return the per-agent dict from team.json. Raises KeyError on miss."""
    agents = load_team().get("agents", {})
    if agent not in agents:
        raise KeyError(f"agent {agent!r} not in team.json")
    return dict(agents[agent])


def agent_cli(agent: str) -> str:
    """Return the CLI identifier for an agent (defaults to 'claude-code')."""
    return agent_config(agent).get("cli", "claude-code")


def agent_model(agent: str) -> str:
    """Resolve model: agent-specific → CLAUDETEAM_DEFAULT_MODEL → team default → 'opus'."""
    cfg = agent_config(agent)
    if cfg.get("model"):
        return cfg["model"]
    env_default = os.environ.get("CLAUDETEAM_DEFAULT_MODEL", "").strip()
    if env_default:
        return env_default
    return load_team().get("default_model", "opus")


# ── runtime_config.json ──────────────────────────────────────────


def load_runtime_config() -> dict:
    return read_json(runtime_config_file(), {})


def save_runtime_config(cfg: dict) -> None:
    write_json(runtime_config_file(), cfg)


def chat_id() -> str:
    return load_runtime_config().get("chat_id", "")


def lark_profile() -> str:
    """Resolve the lark-cli profile name; env beats file."""
    env = os.environ.get("LARK_CLI_PROFILE", "").strip()
    if env:
        return env
    return load_runtime_config().get("lark_profile", "")
