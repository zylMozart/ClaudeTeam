"""Generate unique lark-cli profile names for multi-team isolation.

Single source of truth for the ``{session}-{hash6}`` profile naming scheme that
keeps each ClaudeTeam clone on its own lark-cli profile (and therefore its own
Feishu App + WebSocket event stream). Used by:

  • ``scripts/setup.py`` fresh-deploy path (auto-generates the recommended name).
  • ``scripts/setup.py rotate-profile`` subcommand (voluntary migration for
    legacy deployments that still use the default ``appId`` profile).

History: this replaces the per-host single-team auto-exempt branch introduced
in 9001cd0 (reverted in 770f20c). Boss decision: every deployment goes through
multi-team isolation, no special-casing.
"""
from __future__ import annotations

import hashlib
from pathlib import Path

HASH_LEN = 6


def generate_unique_profile_name(session: str, project_root) -> str:
    """Return ``{session}-{hash6}`` derived from session + resolved path.

    - **Stable**: same ``project_root`` always produces the same name, so a
      second ``setup.py`` run reads back the same profile.
    - **Unique**: two clones at different paths get different ``hash6`` slices
      even when ``session`` is identical (default ``claudeteam`` collisions).
    - **Human-readable**: ``session`` prefix lets operators map a profile back
      to a team without resolving the hash.

    Args:
        session: ``team.json`` ``session`` field — usually the tmux session
            name (e.g. ``"claudeteam"``).
        project_root: clone root directory; ``str`` or ``pathlib.Path``.
            Resolved to absolute path before hashing.

    Returns:
        ``"<session>-<hex6>"`` where ``hex6`` = first 6 hex chars of
        ``sha1(resolved_root)``.

    Examples:
        >>> generate_unique_profile_name("claudeteam", "/tmp/clone-A")  # doctest: +SKIP
        'claudeteam-a3f7b9'
        >>> generate_unique_profile_name("claudeteam", "/tmp/clone-B")  # doctest: +SKIP
        'claudeteam-7e2c1d'
    """
    resolved = str(Path(project_root).resolve())
    digest = hashlib.sha1(resolved.encode("utf-8")).hexdigest()[:HASH_LEN]
    return f"{session}-{digest}"
