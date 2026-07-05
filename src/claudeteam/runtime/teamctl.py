"""Team-level lifecycle control shared by the `/shutdown` & `/restart` chat
slash commands and their `claudeteam team-shutdown | team-restart` runners.

Three concerns live here:

1. The default-deny SAFETY GATE. Chat-driven team teardown/restart is OFF
   unless the deployment's claudeteam.toml explicitly opts in with
       [controls]
       allow_lifecycle_slash = true
   The live maintenance team simply never sets it, so a `/shutdown` typed
   there is refused even if every other guard were somehow bypassed. This
   is defense-in-depth ON TOP OF classify_event's cross-chat drop
   (router.py: an event whose chat_id != this team's is dropped
   "cross_team" before it can ever become a slash) and the container
   boundary (down/up only touch config.session_name() + local pidfiles).

2. DETACHED execution. `down` SIGTERMs the router daemon that is handling
   the slash, so the down/up sequence must run in a process that OUTLIVES
   the router — a new-session child (start_new_session=True), never an
   in-router thread (which dies with the daemon it lives in).

3. The completion NOTIFY. After down/up the router is gone, so the slash
   handler can't post the outcome — the detached runner posts it instead.
"""
from __future__ import annotations

import subprocess
import sys

from claudeteam.runtime import config, tunables


_LIFECYCLE_FLAG = "controls.allow_lifecycle_slash"
_LOGIN_FLAG = "controls.allow_login_slash"


def lifecycle_slash_enabled() -> bool:
    """True only when the deployment explicitly opted into chat-driven team
    teardown/restart (/shutdown, /restart). Default-deny: absent / false /
    any non-control team (including the live maintenance group) → False."""
    return bool(tunables.tunable(_LIFECYCLE_FLAG, False))


def login_slash_enabled() -> bool:
    """True only when the deployment explicitly opted into chat-driven CLI
    re-auth (/login). Kept SEPARATE from the lifecycle flag so /shutdown &
    /restart can be enabled while /login stays inert until the container's
    credentials are isolated from the host — otherwise a /login write would
    clobber the operator's personal host credentials."""
    return bool(tunables.tunable(_LOGIN_FLAG, False))


_LOGIN_ALLOWED_CLIS_FLAG = "controls.login_allowed_clis"
# CLIs whose creds are isolated from the host (per-agent HOME / dedicated
# cred file), so a /login re-auth there can't reach the operator's personal
# credentials. kimi (shared ~/.kimi) and gemini/qwen are intentionally
# absent → hard-refused until isolated + added here.
_DEFAULT_LOGIN_CLIS = "claude-code,codex-cli"


def login_allowed_clis() -> frozenset:
    """The set of CLIs /login may re-auth in THIS deployment — those whose
    credentials are isolated from the host. A CLI not in this set is
    HARD-REFUSED (not just warned): a /login write to a host-mounted cred
    dir would clobber the operator's personal credentials. Tunable
    `controls.login_allowed_clis` (comma-separated); defaults to the set
    known to be isolated (claude-code, codex-cli)."""
    val = tunables.tunable(_LOGIN_ALLOWED_CLIS_FLAG, _DEFAULT_LOGIN_CLIS)
    if isinstance(val, str):
        items = [s.strip() for s in val.split(",") if s.strip()]
    else:  # toml may already give a list
        items = [str(s).strip() for s in val if str(s).strip()]
    return frozenset(items)


def _cli_argv() -> list[str]:
    """How to re-invoke this package's CLI from a detached child. Uses the
    running interpreter + module entrypoint so it works whether or not a
    `claudeteam` console-script is on PATH inside the container."""
    return [sys.executable, "-m", "claudeteam.cli"]


def spawn_detached(subcommand: list[str], *, popen=subprocess.Popen) -> None:
    """Fire-and-forget `claudeteam <subcommand>` in its OWN session so a
    subsequent `down` (which SIGTERMs the router's process group) can't
    take it down with the daemon. stdio is detached to DEVNULL — the
    runner reports back via `notify`, not stdout."""
    from claudeteam.util import detached_popen_kwargs
    popen(
        _cli_argv() + list(subcommand),
        stdin=subprocess.DEVNULL,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
        **detached_popen_kwargs(),
    )


def notify(card: dict) -> None:
    """Best-effort completion card to the team chat. Called by the detached
    runner AFTER down/up, when the router that would normally post is dead.
    Any failure (no chat_id, lark error) is swallowed: a missing receipt
    must never make a finished lifecycle op look failed."""
    try:
        from claudeteam.feishu import chat
        cid = config.chat_id()
        if cid:
            chat.send_card(cid, card, profile=config.lark_profile())
    except Exception:
        pass
