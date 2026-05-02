"""`claudeteam say <agent> <message> [--reply <message_id>]`

Post a chat message as `<agent>`.  Default identity is bot; pass
`--as user` to post as the logged-in lark-cli user.

The message is also mirrored to the local inbox (so the audit log keeps
a copy) — pass `--no-local` to skip that.

Exits non-zero if `chat_id` is unset (run setup or set runtime_config.json).
"""
from __future__ import annotations

from dataclasses import dataclass

from claudeteam.feishu import chat as feishu_chat
from claudeteam.runtime import config
from claudeteam.store import local_facts
from claudeteam.util import error_exit, usage_error


USAGE = (
    "usage: claudeteam say <agent> <message> "
    "[--reply <message_id>] [--as user|bot] [--no-local]"
)


@dataclass(frozen=True)
class _Args:
    agent: str
    message: str
    reply_to: str = ""
    as_user: bool = False
    local: bool = True


def _parse(argv: list[str]) -> _Args | None:
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
    return _Args(agent=agent, message=" ".join(rest), **opts)


def main(argv: list[str]) -> int:
    args = _parse(argv)
    if args is None:
        return usage_error(USAGE)

    chat = config.chat_id()
    if not chat:
        return error_exit("❌ chat_id not set in runtime_config.json")

    profile = config.lark_profile()

    local_facts.touch_heartbeat(args.agent)
    if args.local:
        local_facts.append_log(args.agent, "say", args.message)

    result = feishu_chat.send_text(
        chat, f"[{args.agent}] {args.message}",
        profile=profile,
        as_user=args.as_user,
        reply_to=args.reply_to,
    )
    if result is None:
        return error_exit(f"❌ Feishu send failed for {args.agent}")

    msg_id = result.get("message_id", "")
    print(f"✅ {args.agent} → chat ({msg_id})")
    return 0
