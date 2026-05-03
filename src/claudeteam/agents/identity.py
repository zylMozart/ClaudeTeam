"""Render per-agent identity markdown.

Each agent gets a small markdown file at
    $CLAUDETEAM_STATE_DIR/agents/<name>/identity.md
that the agent's CLI reads on demand to learn:
  - who it is and what role
  - which command format to use for talking back
  - which CLI it's running under (so adapter quirks like Codex's
    M-Enter don't surprise it)

The text is interpolated from the agent's team.json entry — there's no
external template file to edit; the canonical copy lives in this module.
"""
from __future__ import annotations

from pathlib import Path

from claudeteam.runtime import config, paths
from claudeteam.store import memory
from claudeteam.util import atomic_write_text


# Shared section: every role's identity needs this guardrail. Keeping it
# in one constant means any tweak (new env vars, more failure modes) only
# happens once and both bodies stay in sync automatically.
_WORKDIR_RULE = """\
## Working directory rule (CRITICAL)

Run all `claudeteam …` commands from your **current working directory**
— do NOT `cd` anywhere. `runtime_config.json` (which has the `chat_id`
and `lark_profile`) lives next to where you were spawned; if you
`cd /elsewhere && claudeteam say …`, the command runs against a
different `runtime_config.json` (or none) and fails with
`chat_id not set`."""


_MANAGER_BODY = """\
# {name} — {role}

You are **{name}**, the team manager.  Your role is **{role}** running on
**{cli}** (model: `{model}`).

## Your job
- Receive messages from the boss in the Feishu group chat (router routes
  them to your tmux pane).
- Break tasks down and dispatch to workers via `claudeteam send`.
- Track progress with `claudeteam status` and `claudeteam task`.
- Reply to the boss in the chat with `claudeteam say manager "<reply>"`.

## Argument-order contract (READ CAREFULLY — ARGS MATTER)

```
✅  claudeteam send <recipient> <sender> "<message>" [priority]
       e.g. claudeteam send worker_cc manager "请处理 X 任务" 高
            (recipient = worker_cc, sender = you = manager)

✅  claudeteam say <agent> "<message>"
       e.g. claudeteam say manager "已收到，开始处理"
            (agent = you = manager — first arg is who's speaking)
```

❌ Do NOT swap recipient/sender on `send`.  ❌ Do NOT drop the agent
name on `say`.

{workdir_rule}

## Inbox & status
- `claudeteam inbox manager` — your unread messages
- `claudeteam read <local_id>` — mark a message read
- `claudeteam status manager 进行中 "current task"` — set your own state
- `claudeteam team` — see everyone's current status
"""


_WORKER_BODY = """\
# {name} — {role}

You are **{name}**, a team worker.  Your role is **{role}** running on
**{cli}** (model: `{model}`).

## Your job
- Pick up tasks from `claudeteam inbox {name}`.
- Mark them read once you start: `claudeteam read <local_id>`.
- Report progress to the manager: `claudeteam send manager {name} "<update>"`.
- Update your own status: `claudeteam status {name} 进行中 "<task>"`.
- When done, `claudeteam task done <T-id>` if a task tracker entry is open.

## Argument-order contract (READ CAREFULLY)

```
✅  claudeteam send <recipient> <sender> "<message>" [priority]
       you are the SENDER:
       claudeteam send manager {name} "step 1 done" 中

✅  claudeteam say <agent> "<message>"
       you are the AGENT — first arg is your own name:
       claudeteam say {name} "进度同步: ..."
```

❌ Do NOT type `claudeteam say "<message>"` (missing agent name); the
   command rejects with `usage:` line.
❌ Do NOT swap recipient/sender on `send`.

{workdir_rule}

## Quick reference
- `claudeteam inbox {name}` — unread
- `claudeteam workspace {name}` — your audit log tail
- `claudeteam log {name} <kind> "<note>"` — append an audit entry
"""


def render(agent: str, *, role: str | None = None,
           cli: str | None = None, model: str | None = None) -> str:
    """Return the identity markdown text for `agent`.

    Defaults missing fields from team.json so callers can call this with
    just the agent name in production, or override every field for tests.
    """
    cfg = config.agent_config(agent) if any(v is None for v in (role, cli, model)) else {}
    role = role if role is not None else (cfg.get("role") or agent)
    cli = cli if cli is not None else (cfg.get("cli") or "claude-code")
    model = model if model is not None else (cfg.get("model") or "")
    body = _MANAGER_BODY if agent == "manager" else _WORKER_BODY
    return body.format(name=agent, role=role, cli=cli, model=model,
                       workdir_rule=_WORKDIR_RULE)


def init_prompt(agent: str) -> str:
    """On-spawn / on-clear / on-reidentify prompt: inject this into an
    agent's pane so it loads its identity, checks inbox, and reports for
    duty. Without this, a freshly-spawned claude-code sits at an empty
    prompt and never knows it's "manager" or "worker_cc".

    Round-84: append the agent's recent durable memory (if any) so a
    pane that's been /clear-ed or restarted picks up where it left off
    instead of losing all task continuity. Empty memory → no extra
    section appears (avoid noise on a brand-new agent).
    """
    base = (
        f"You are {agent}. Read agents/{agent}/identity.md, then run:\n"
        f"  claudeteam inbox {agent}\n"
        f"  claudeteam status {agent} 进行中 \"ready\"\n"
        f"Acknowledge with one line: name, state, unread count."
    )
    recall = memory.render_for_prompt(agent)
    if not recall:
        return base
    return f"{base}\n\n{recall}\n\n继续之前未完成的工作；如已完成则确认并待命。"


def identity_path(agent: str) -> Path:
    """Where the rendered identity for `agent` lives on disk."""
    return paths.state_dir() / "agents" / agent / "identity.md"


def write(agent: str, *, role: str | None = None,
          cli: str | None = None, model: str | None = None) -> Path:
    """Render and persist the identity file; return its path."""
    target = identity_path(agent)
    atomic_write_text(target, render(agent, role=role, cli=cli, model=model))
    return target
