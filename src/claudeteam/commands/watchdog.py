"""`claudeteam watchdog`

Long-running supervisor that keeps the router (and any future daemons)
alive.  Runs `runtime.watchdog.supervise` every CHECK_INTERVAL seconds
until SIGTERM / Ctrl-C.

Self-locks via state_dir/watchdog.pid so two watchdogs can't fight.

Round-82: when a supervised daemon enters cooldown (max_retries respawns
failed) the watchdog posts a Feishu message to the team chat so the boss
sees the death without having to tail the watchdog log. Best-effort —
chat-send failures are swallowed (don't kill the watchdog).
"""
from __future__ import annotations

import signal
import sys
import time

from claudeteam.feishu import chat as _chat
from claudeteam.runtime import config, paths, pidlock, watchdog
from claudeteam.util import maybe_print_help


CHECK_INTERVAL_SECS = 30


def _make_alert_fn():
    """Build the alert callable handed to `supervise`. Captures chat_id +
    profile at construction time (cheap reads of runtime_config.json)
    so each cooldown event sends without re-reading config.

    Returns None when chat_id is unset — alerts are pointless without a
    delivery target, and a None alert_fn is the supervise default."""
    chat_id = config.chat_id()
    if not chat_id:
        return None
    profile = config.lark_profile()

    def alert(name: str, failed_at: int, cooldown_secs: int) -> None:
        text = (
            f"🚨 watchdog: daemon {name} entered {cooldown_secs}s cooldown "
            f"after {failed_at} failed respawns. "
            f"`claudeteam health` for current state; check daemon log for root cause."
        )
        _chat.send_text(chat_id, text, profile=profile, as_user=False)

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
