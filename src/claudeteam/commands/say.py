"""`claudeteam say <agent> <message> [--reply <message_id>]`

Post a chat message as `<agent>`.  Default identity is bot; pass
`--as user` to post as the logged-in lark-cli user.  A persistent default
can be set via `CLAUDETEAM_LARK_SEND_AS=user|bot` for the whole shell.

The message is also mirrored to the local inbox (so the audit log keeps
a copy) — pass `--no-local` to skip that.

Exits non-zero if `chat_id` is unset (run setup or set runtime_config.json).
"""
from __future__ import annotations

import sys
from dataclasses import dataclass

from claudeteam.feishu import chat as feishu_chat
from claudeteam.feishu.cards import simple_card
from claudeteam.runtime import config
from claudeteam.store import local_facts
from claudeteam.util import env_str, error_exit, pop_bool_flag, pop_flag, usage_error


USAGE = (
    "usage: claudeteam say <agent> <message> "
    "[--reply <message_id>] [--as user|bot] [--no-local] "
    "[--to user|manager|worker_<name>]"
)


# Card colors per agent role conventions. manager → blue (default visual
# weight), worker_* → green (status updates), boss-tagged → grey (just
# context). Unknown agents fall back to blue. Round-99: extracted so a
# future deployment with custom roles can override without touching the
# render code.
_AGENT_CARD_COLORS = {
    "manager": "blue",
}

# Default emoji per agent name. Used when claudeteam.toml doesn't
# provide an explicit `emoji` field. The card sender header
# (`{emoji} {agent} · {role}`) signals who's talking at a glance.
_DEFAULT_AGENT_EMOJI = {
    "manager": "🎯",
    "worker_cc": "💎",
    "worker_codex": "🟦",
    "worker_kimi": "🟧",
    "worker_gemini": "🟩",
    "worker_qwen": "🟪",
}


def _role_of(name: str) -> str:
    """Map agent name → role bucket used by chat.publish keys.
    Convention: 'manager' → manager; 'worker_*' → worker; 'user' → user;
    anything else → user (safe default; "对老板说" is the most common
    intent when receiver is unrecognized)."""
    if name == "manager":
        return "manager"
    if name == "user" or not name:
        return "user"
    if name.startswith("worker"):
        return "worker"
    return "user"


def _publish_allowed(sender: str, to_target: str) -> bool:
    """Look up publish rule for sender→receiver, with agent-level override.

    Priority:
      1. team.agents.<sender>.publish_overrides.{key}  (single-agent override)
      2. chat.publish.{key}                             (team-wide tunable)
      3. default True                                    (preserves pre-Step-3 behavior)

    `key` = "{sender_role}_to_{receiver_role}".

    "always" is treated as True — schema uses it as a "don't silence"
    hint but the runtime semantic is just "send".

    Agent-level override is for cases like "I want worker_cc 完工卡进群,
    but worker_codex 完工卡静默" — set worker_codex.publish_overrides
    = {worker_to_user = false} without touching the global rule.
    """
    from claudeteam.runtime import tunables
    sender_role = _role_of(sender)
    receiver_role = _role_of(to_target)
    key = f"{sender_role}_to_{receiver_role}"

    # 1. Agent-level override
    try:
        agent_cfg = config.agent_config(sender)
    except KeyError:
        agent_cfg = {}
    overrides = agent_cfg.get("publish_overrides") or {}
    if key in overrides:
        v = overrides[key]
        return v == "always" or bool(v)

    # 2. Global tunable
    val = tunables.tunable(f"chat.publish.{key}", True)
    if val == "always":
        return True
    return bool(val)


def _color_for(agent: str, cfg_color: str | None = None) -> str:
    """Resolve card header color. Per-agent `color` field in team.json
    wins; else manager → blue, worker_* → green, fallback blue."""
    if cfg_color:
        return cfg_color
    if agent in _AGENT_CARD_COLORS:
        return _AGENT_CARD_COLORS[agent]
    if agent.startswith("worker"):
        return "green"
    return "blue"


def _emoji_for(agent: str, cfg_emoji: str | None = None) -> str:
    """Resolve sender emoji. team.json `emoji` field wins, otherwise
    fall back to `_DEFAULT_AGENT_EMOJI`, otherwise ⚙️ (system)."""
    if cfg_emoji:
        return cfg_emoji
    return _DEFAULT_AGENT_EMOJI.get(agent, "⚙️")


def _agent_card_title(agent: str, cfg: dict) -> str:
    """Card title format ported from `main`'s `_agent_card_title`:
    `{emoji} {agent} · {role}` — English agent id + Chinese role at a
    glance, no more bare `[agent]` brackets that boss flagged as too
    bland."""
    emoji = _emoji_for(agent, cfg.get("emoji"))
    role = cfg.get("role") or "系统"
    return f"{emoji} {agent} · {role}"


@dataclass(frozen=True)
class _Args:
    agent: str
    message: str
    reply_to: str = ""
    as_user: bool = False
    local: bool = True
    to: str = "user"   # receiver hint for chat.publish filter; default
                       # "user" preserves backwards-compat for callers
                       # that don't pass --to (manager → user is the
                       # typical case)


def _parse(argv: list[str]) -> _Args | None:
    if len(argv) < 2:
        return None
    rest = list(argv)
    # `--card` / `--no-card` are accepted but ignored — every
    # `claudeteam say` posts a v2 card. The flags are consumed for
    # backwards-compat with operators / docs that still pass them.
    pop_bool_flag(rest, "--card")
    pop_bool_flag(rest, "--no-card")
    no_local = pop_bool_flag(rest, "--no-local")
    reply_to = pop_flag(rest, "--reply") or ""
    as_explicit = pop_flag(rest, "--as")
    to_explicit = pop_flag(rest, "--to") or "user"
    if "--reply" in rest or "--as" in rest or "--to" in rest:
        return None  # flag present but value missing
    if len(rest) < 2:
        return None
    agent = rest[0]
    rest = rest[1:]
    # `feishu.send_as` cascade: --as flag > legacy env > tunable > "bot" default.
    if as_explicit is not None:
        as_value = as_explicit
    else:
        legacy = env_str("CLAUDETEAM_LARK_SEND_AS")
        if legacy:
            as_value = legacy
        else:
            from claudeteam.runtime import tunables
            as_value = str(tunables.tunable("feishu.send_as", "bot"))
    if not rest:
        return None
    return _Args(
        agent=agent,
        message=" ".join(rest),
        reply_to=reply_to,
        as_user=(as_value == "user"),
        local=not no_local,
        to=to_explicit,
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
        # Audit log is best-effort — a disk-full or permission-denied
        # error here should NOT block the chat send (the boss is
        # waiting for the message to land in the group; losing the
        # local audit row is a smaller cost than losing the message).
        try:
            local_facts.append_log(args.agent, "say", args.message)
        except OSError as e:
            print(f"  ⚠️ audit log write failed for {args.agent}: {e}",
                  file=sys.stderr)

    # Resolve agent's role + emoji + color from claudeteam.toml. Used
    # for the card title (`{emoji} {agent} · {role}`) and for color
    # override. Missing config falls back to the per-agent default
    # tables defined at the top of this file.
    try:
        agent_cfg = config.agent_config(args.agent)
    except KeyError:
        agent_cfg = {}

    # Every `claudeteam say` sends a v2 card. `reply_to` is silently
    # ignored because Feishu interactive cards don't thread.
    if args.reply_to:
        print(f"  ⚠️ --reply ignored (Feishu cards don't thread)",
              file=sys.stderr)
    title = _agent_card_title(args.agent, agent_cfg)
    # `card_color` is the new field name (more specific than just "color");
    # fall back to legacy "color" so old team.json keeps working.
    cfg_color = agent_cfg.get("card_color") or agent_cfg.get("color")
    card = simple_card(title, args.message,
                        color=_color_for(args.agent, cfg_color))

    # Step 3: chat.publish filter — operator can silence specific
    # sender→receiver channels via toml (default all true = preserve
    # pre-Step-3 behavior). Audit log was already written above
    # regardless of publish state, so silenced messages still leave a
    # trail.
    if not _publish_allowed(args.agent, args.to):
        from claudeteam.runtime import tunables
        sender_role = _role_of(args.agent)
        receiver_role = _role_of(args.to)
        key = f"chat.publish.{sender_role}_to_{receiver_role}"
        print(f"📝 {args.agent} → silenced by [{key}]=false; logged only")
        return 0

    result = feishu_chat.send_card(
        chat, card,
        profile=profile,
        as_user=args.as_user,
    )
    if result is None:
        return error_exit(f"❌ Feishu send failed for {args.agent}")

    msg_id = result.get("message_id", "")
    print(f"✅ {args.agent} → chat (message_id={msg_id})")
    return 0
