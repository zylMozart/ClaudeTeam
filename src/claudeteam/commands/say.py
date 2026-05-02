"""`claudeteam say <agent> <message> [--reply <message_id>]`

Post a chat message as `<agent>`.  Default identity is bot; pass
`--as user` to post as the logged-in lark-cli user.

The message is also mirrored to the local inbox (so the audit log keeps
a copy) — pass `--no-local` to skip that.

Exits non-zero if `chat_id` is unset (run setup or set runtime_config.json).
"""
from __future__ import annotations

import sys

from claudeteam.feishu import chat as feishu_chat
from claudeteam.runtime import config
from claudeteam.store import local_facts
from claudeteam.util import usage_error


USAGE = (
    "usage: claudeteam say <agent> <message> "
    "[--reply <message_id>] [--as user|bot] [--no-local]"
)


def _parse(argv: list[str]) -> tuple[str, str, dict] | None:
    if len(argv) < 2:
        return None
    agent = argv[0]
    rest = list(argv[1:])
    opts = {"reply_to": "", "as_user": False, "local": True}
    for flag, key in [("--reply", "reply_to"), ("--as", "_as")]:
        if flag in rest:
            i = rest.index(flag)
            if i + 1 >= len(rest):
                return None
            val = rest[i + 1]
            if key == "_as":
                opts["as_user"] = val == "user"
            else:
                opts[key] = val
            del rest[i:i + 2]
    if "--no-local" in rest:
        opts["local"] = False
        rest.remove("--no-local")
    if not rest:
        return None
    return agent, " ".join(rest), opts


def main(argv: list[str]) -> int:
    parsed = _parse(argv)
    if parsed is None:
        return usage_error(USAGE)
    agent, message, opts = parsed

    chat = config.chat_id()
    if not chat:
        print("❌ chat_id not set in runtime_config.json", file=sys.stderr)
        return 1

    profile = config.lark_profile()

    local_facts.touch_heartbeat(agent)
    if opts["local"]:
        local_facts.append_log(agent, "say", message)

    result = feishu_chat.send_text(
        chat, f"[{agent}] {message}",
        profile=profile,
        as_user=opts["as_user"],
        reply_to=opts["reply_to"],
    )
    if result is None:
        print(f"❌ Feishu send failed for {agent}", file=sys.stderr)
        return 1

    msg_id = result.get("message_id", "")
    print(f"✅ {agent} → chat ({msg_id})")
    return 0
