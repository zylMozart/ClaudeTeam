"""Pure process-cmdline matching helpers for watchdog."""
from __future__ import annotations


def is_lark_subscribe_cmdline(cmdline: str | None) -> bool:
    text = cmdline or ""
    return "lark-cli" in text and "event" in text and "+subscribe" in text
