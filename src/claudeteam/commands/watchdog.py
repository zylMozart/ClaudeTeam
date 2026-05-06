"""`claudeteam watchdog`

Long-running supervisor that keeps the router (and any future daemons)
alive.  Runs `runtime.watchdog.supervise` every CHECK_INTERVAL seconds
until SIGTERM / Ctrl-C.

Self-locks via state_dir/watchdog.pid so two watchdogs can't fight.

Cooldown alerts:
- R82 added alert_fn: when a supervised daemon enters cooldown
  (max_retries respawns failed) the watchdog posts to Feishu chat so
  the boss sees the death without tailing the watchdog log.
- R98 upgraded the alert from plain text to a red Feishu card with a
  3-step recovery checklist (`claudeteam health` / read daemon log /
  `claudeteam down && up` after fix). Falls back to send_text on card
  schema rejection so the alert still lands.
- Returns None alert_fn when chat_id is unset — alerts are pointless
  without a delivery target. Boot banner says "no chat alerts" in
  that case so operator knows.

R173: claude OAuth keep-alive
- Boss flagged 2026-05-05 that boss-message routing died because the
  bind-mounted claude .credentials.json expired during idle and the
  in-pane claude only refreshes on-API-call (not idle). Watchdog now
  proactively reads `expiresAt` every CRED_CHECK_INTERVAL_SECS; if
  the token's < CRED_REFRESH_AHEAD seconds from expiry, run
  `claude -p "Return only OK"` once. That triggers claude to refresh
  the token in-place (file is bind-mounted RW so the new token
  persists back to host). All agent panes share the same file via
  per-agent symlink, so one refresh covers the whole team.

All alert paths are best-effort: chat send / card send failures are
swallowed at the alert_fn level (and runtime/watchdog's supervise
also try/excepts alert_fn). A broken alert path mustn't kill the
supervisor.
"""
from __future__ import annotations

import json
import signal
import subprocess
import sys
import time
from pathlib import Path

from claudeteam.feishu import chat as _chat
from claudeteam.feishu.cards import simple_card
from claudeteam.runtime import config, paths, pidlock, watchdog
from claudeteam.util import maybe_print_help


CHECK_INTERVAL_SECS = 30
# R173: keep-alive cadence for claude OAuth refresh. Claude tokens
# typically expire ~12h; we refresh whenever < 30min remain so we
# never serve a request to an expired token. Check every 5min so the
# refresh fires within ±5min of the threshold.
CRED_CHECK_INTERVAL_SECS = 300
CRED_REFRESH_AHEAD_SECS = 1800
_CRED_PATH = Path("/root/.claude/.credentials.json")


def _make_alert_fn():
    """Build the alert callable handed to `supervise`. Captures chat_id +
    profile at construction time (cheap reads of runtime_config.json)
    so each cooldown event sends without re-reading config.

    Returns None when chat_id is unset — alerts are pointless without a
    delivery target, and a None alert_fn is the supervise default.

    Round-98: send as red Feishu card (was plain text in R82) so the
    cooldown event is visually distinct from normal /team / /health
    cards in the chat. Falls back to text if send_card raises.
    """
    chat_id = config.chat_id()
    if not chat_id:
        return None
    profile = config.lark_profile()

    def alert(name: str, failed_at: int, cooldown_secs: int) -> None:
        title = f"🚨 watchdog: {name} entered cooldown"
        body = (
            f"daemon **{name}** entered **{cooldown_secs}s cooldown** "
            f"after **{failed_at}** failed respawns.\n\n"
            f"- `claudeteam health` for current state\n"
            f"- check daemon log for root cause\n"
            f"- after fix: `claudeteam down && claudeteam up`"
        )
        card = simple_card(title, body, color="red")
        try:
            _chat.send_card(chat_id, card, profile=profile, as_user=False)
        except Exception as e:
            # Card delivery shouldn't kill the watchdog. Fall back to
            # plain text so the alert still lands somehow; if THAT also
            # fails the supervise outer try/except logs it.
            print(f"  ⚠️ watchdog: card alert send failed ({e}); falling back to text")
            _chat.send_text(chat_id,
                            f"🚨 watchdog: {name} entered {cooldown_secs}s cooldown "
                            f"after {failed_at} failed respawns",
                            profile=profile, as_user=False)

    return alert


def main(argv: list[str]) -> int:
    if maybe_print_help(argv, "usage: claudeteam watchdog"):
        return 0
    pid_file = paths.watchdog_pid_file()
    if not pidlock.acquire(pid_file, name="watchdog"):
        return 1
    signal.signal(signal.SIGTERM, lambda *_: sys.exit(0))

    specs = watchdog.default_specs()
    states: dict = {}
    alert_fn = _make_alert_fn()
    alert_msg = "with chat alerts" if alert_fn else "no chat alerts (chat_id unset)"
    print(f"🐕 watchdog supervising {[s.name for s in specs]} every {CHECK_INTERVAL_SECS}s ({alert_msg})")

    last_cred_check = 0.0
    try:
        while True:
            watchdog.supervise(specs, states, alert_fn=alert_fn)
            now = time.time()
            if now - last_cred_check >= CRED_CHECK_INTERVAL_SECS:
                _maybe_refresh_claude_oauth(now)
                last_cred_check = now
            time.sleep(CHECK_INTERVAL_SECS)
    except KeyboardInterrupt:
        print("watchdog stopped")
        return 0
    finally:
        pidlock.release(pid_file)


def _maybe_refresh_claude_oauth(now: float) -> None:
    """If the bind-mounted claude .credentials.json expires within
    CRED_REFRESH_AHEAD_SECS, force-refresh by spawning a brief
    `claude -p "Return only OK"`. That subprocess hits the Anthropic
    API which makes claude rotate the access token in-place. File is
    bind-mounted RW so the new token persists to host.

    Best-effort: any failure (file missing, parse error, claude crashes,
    network down) logs a warning but doesn't kill the watchdog. Worst
    case the boss still sees expired-token errors next cycle and runs
    `make creds` manually.
    """
    if not _CRED_PATH.exists():
        # Host deploy (macOS): no /root mount, claude OAuth lives in
        # keychain not file. Silent skip — printing every 5min spams
        # watchdog.log with hundreds of false alarms.
        return
    try:
        oauth = json.loads(_CRED_PATH.read_text())["claudeAiOauth"]
        expires_ms = int(oauth.get("expiresAt", 0))
    except (OSError, ValueError, KeyError) as e:
        print(f"  ⚠️ cred-refresh: read {_CRED_PATH} failed: {e}")
        return
    remaining = expires_ms / 1000 - now
    if remaining > CRED_REFRESH_AHEAD_SECS:
        return  # plenty of time; skip
    print(f"  🔑 claude token expires in {int(remaining)}s — forcing refresh")
    try:
        r = subprocess.run(
            ["claude", "-p", "Return only OK"],
            capture_output=True, text=True, timeout=60,
        )
    except (subprocess.TimeoutExpired, OSError) as e:
        print(f"  ⚠️ cred-refresh: `claude -p` failed: {e}")
        return
    if r.returncode != 0:
        snippet = (r.stderr or r.stdout or "").strip()[:120]
        print(f"  ⚠️ cred-refresh: claude rc={r.returncode}: {snippet}")
        return
    print("  ✅ claude token refreshed")
