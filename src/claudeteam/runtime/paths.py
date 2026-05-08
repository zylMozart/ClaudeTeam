#!/usr/bin/env python3
"""Runtime state path helpers.

Mutable process state must not live under scripts/ because production hardened
containers mount the application code read-only.  State defaults to
CLAUDETEAM_STATE_DIR when set, then /app/state for container installs, and
workspace/shared/state for local development.
"""
from __future__ import annotations

import os
from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parents[3]
SCRIPT_DIR = PROJECT_ROOT / "scripts"


def runtime_state_dir() -> Path:
    env_dir = os.environ.get("CLAUDETEAM_STATE_DIR", "").strip()
    if env_dir:
        return Path(env_dir)
    if str(PROJECT_ROOT) == "/app" or str(PROJECT_ROOT).startswith("/app/"):
        return Path("/app/state")
    return PROJECT_ROOT / "workspace" / "shared" / "state"


def runtime_state_file(name: str) -> str:
    path = runtime_state_dir() / name
    path.parent.mkdir(parents=True, exist_ok=True)
    return str(path)


def legacy_script_state_file(name: str) -> str:
    return str(SCRIPT_DIR / name)


def ensure_parent(path: str) -> None:
    Path(path).parent.mkdir(parents=True, exist_ok=True)
