"""`claudeteam watchdog`

Long-running supervisor that keeps the router (and any future daemons)
alive. Runs `runtime.watchdog.supervise` every
`watchdog.check_interval_s` seconds (claudeteam.toml; default 30) until
SIGTERM / Ctrl-C.

Self-locks via state_dir/watchdog.pid so two watchdogs can't fight.

Cooldown alerts:
- When a supervised daemon enters cooldown (max_retries respawns
  failed), the watchdog posts to Feishu chat so the boss sees the
  death without tailing the watchdog log.
- The alert is a red Feishu card with a 3-step recovery checklist
  (`claudeteam health` / read daemon log / `claudeteam down && up`
  after fix). Falls back to plain `send_text` on card schema
  rejection so the alert still lands.
- alert_fn is None when chat_id is unset — alerts are pointless
  without a delivery target; boot banner says "no chat alerts" so
  the operator knows.

Claude OAuth keep-alive:
- Bind-mounted claude .credentials.json expires during idle and
  the in-pane claude only refreshes on API call (not idle), which
  killed boss-message routing after long silences. Watchdog now
  proactively reads `expiresAt` every
  `watchdog.cred_check_interval_s` seconds (default 300); if
  the token's < `watchdog.cred_refresh_ahead_s` (default 1800) from
  expiry, run `claude -p "Return only OK"` once. That triggers
  claude to refresh the token in-place (file is bind-mounted RW so
  the new token persists back to host). All agent panes share the
  same file via per-agent symlink, so one refresh covers the whole
  team.

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
from claudeteam.runtime import config, paths, pidlock, tunables, watchdog
from claudeteam.util import maybe_print_help


_CRED_PATH = Path.home() / ".claude" / ".credentials.json"
# Resolves to /root/.claude/.credentials.json in Docker (HOME=/root) — same
# path the host-keychain bind-mount lands on — and to ~/.claude/... on host.
# Hardcoding /root broke host non-root deploys: Path("/root/...").exists()
# raised PermissionError (Linux /root is 700) instead of returning False
# under Python 3.10–3.12, killing `claudeteam up`. Caught 2026-05-08.


def _make_alert_fn():
    """Build the alert callable handed to `supervise`. Captures chat_id +
    profile at construction time (cheap reads of runtime_config.json)
    so each cooldown event sends without re-reading config.

    Returns None when chat_id is unset — alerts are pointless without a
    delivery target, and a None alert_fn is the supervise default.

    Sends as a red Feishu card so the cooldown event is visually
    distinct from normal /team / /health cards. Falls back to plain
    text if send_card raises (schema mismatch on older lark builds).
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
        from claudeteam.runtime import tunables
        alarm_color = str(tunables.tunable("router.alarm_card_color", "red"))
        card = simple_card(title, body, color=alarm_color)
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
    # Print before exit so the operator sees *why* the watchdog stopped.
    # The bare `sys.exit(0)` left silence that was indistinguishable from
    # an abrupt SIGKILL (no exit-cause logging) and made post-mortems
    # impossible — `claudeteam health` would just report "pid file
    # present but process dead" with no log breadcrumb.
    def _on_sigterm(*_):
        print("🛑 watchdog: received SIGTERM, exiting cleanly", flush=True)
        sys.exit(0)
    signal.signal(signal.SIGTERM, _on_sigterm)

    specs = watchdog.default_specs()
    states: dict = {}
    alert_fn = _make_alert_fn()
    alert_msg = "with chat alerts" if alert_fn else "no chat alerts (chat_id unset)"
    check_interval_s = int(tunables.tunable("watchdog.check_interval_s", 30))
    cred_check_interval_s = int(tunables.tunable("watchdog.cred_check_interval_s", 300))
    print(f"🐕 watchdog supervising {[s.name for s in specs]} every {check_interval_s}s ({alert_msg})")

    last_cred_check = 0.0
    try:
        while True:
            watchdog.supervise(specs, states, alert_fn=alert_fn)
            now = time.time()
            if now - last_cred_check >= cred_check_interval_s:
                _maybe_refresh_claude_oauth(now)
                last_cred_check = now
            time.sleep(check_interval_s)
    except KeyboardInterrupt:
        print("watchdog stopped")
        return 0
    except BaseException as e:
        # Catch *anything* — Python-level exceptions, SystemExit raised
        # from inside supervise(), even GeneratorExit — and log it before
        # the process dies. Without this branch the only forensic trail
        # was an empty pidfile and a `pid file present but process dead`
        # health-check error.
        import traceback
        print(
            f"💥 watchdog exiting on unhandled {type(e).__name__}: {e!r}",
            flush=True,
        )
        traceback.print_exc()
        return 1
    finally:
        pidlock.release(pid_file)


def _maybe_refresh_claude_oauth(now: float) -> None:
    """If the bind-mounted claude .credentials.json expires within
    `watchdog.cred_refresh_ahead_s` (claudeteam.toml; default 1800),
    force-refresh by spawning a brief `claude -p "Return only OK"`.
    That subprocess hits the Anthropic API which makes claude rotate
    the access token in-place. File is bind-mounted RW so the new
    token persists to host.

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
    cred_refresh_ahead_s = int(tunables.tunable("watchdog.cred_refresh_ahead_s", 1800))
    if remaining > cred_refresh_ahead_s:
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
