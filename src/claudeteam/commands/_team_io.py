"""Shared team.json I/O helpers for commands layer."""
import json
import os
from claudeteam.runtime.config import PROJECT_ROOT


def load_team() -> dict:
    """Load and return team.json as a dict."""
    team_file = os.path.join(PROJECT_ROOT, "team.json")
    with open(team_file) as f:
        return json.load(f)
