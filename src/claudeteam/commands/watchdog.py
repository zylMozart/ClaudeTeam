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

All alert paths are best-effort: chat send / card send failures are
swallowed at the alert_fn level (and runtime/watchdog's supervise
also try/excepts alert_fn). A broken alert path mustn't kill the
supervisor.
"""
from __future__ import annotations

import signal
import sys
import time

from claudeteam.feishu import chat as _chat
from claudeteam.feishu.cards import simple_card
from claudeteam.runtime import config, paths, pidlock, watchdog
from claudeteam.util import maybe_print_help


CHECK_INTERVAL_SECS = 30


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

    try:
        while True:
            watchdog.supervise(specs, states, alert_fn=alert_fn)
            time.sleep(CHECK_INTERVAL_SECS)
    except KeyboardInterrupt:
        print("watchdog stopped")
        return 0
    finally:
        pidlock.release(pid_file)
