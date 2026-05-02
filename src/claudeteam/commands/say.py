"""`claudeteam say <agent> <message> [--reply <message_id>]`

Post a chat message as `<agent>`.  Default identity is bot; pass
`--as user` to post as the logged-in lark-cli user.  A persistent default
can be set via `CLAUDETEAM_LARK_SEND_AS=user|bot` for the whole shell.

The message is also mirrored to the local inbox (so the audit log keeps
a copy) — pass `--no-local` to skip that.

Exits non-zero if `chat_id` is unset (run setup or set runtime_config.json).
"""
from __future__ import annotations

from dataclasses import dataclass

from claudeteam.feishu import chat as feishu_chat
from claudeteam.runtime import config
from claudeteam.store import local_facts
from claudeteam.util import env_str, error_exit, pop_bool_flag, pop_flag, usage_error


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
    reply_to = pop_flag(rest, "--reply") or ""
    as_explicit = pop_flag(rest, "--as")
    if "--reply" in rest or "--as" in rest:
        return None  # flag present but value missing
    # If --as wasn't passed, fall back to CLAUDETEAM_LARK_SEND_AS env var,
    # then to the bot default. Lets operators "set once per shell".
    as_value = as_explicit if as_explicit is not None else env_str("CLAUDETEAM_LARK_SEND_AS")
    no_local = pop_bool_flag(rest, "--no-local")
    if not rest:
        return None
    return _Args(
        agent=agent,
        message=" ".join(rest),
        reply_to=reply_to,
        as_user=(as_value == "user"),
        local=not no_local,
    )


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
