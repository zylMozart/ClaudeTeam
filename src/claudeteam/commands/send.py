"""`claudeteam send <to> <from> <message> [priority] [--no-inject]`

Append a message to the local inbox AND poke the recipient's tmux
pane so they know to read it.

Previously inbox-only with the doc claim "only the Feishu
router can do tmux inject". That broke peer messaging end-to-end —
manager sending to worker_cc wrote a row, but worker_cc had no way
to know unless it polled. Boss-flagged after the 全员报道 e2e where
manager.send → worker_cc went into a dead drop.

Now mirrors the router's apply pattern: append_message + tmux.inject
into the recipient's pane. Recipient's claude (or other CLI) sees a
prompt-style notification and processes inbox proactively. Pass
`--no-inject` to keep the old "silent dead-drop" behaviour for
audit-only writes (caller is putting context for later, not
expecting recipient to read NOW).
"""
from __future__ import annotations

from claudeteam.agents import adapter_for_agent, identity as _identity
from claudeteam.runtime import config, lifecycle, tmux, wake
from claudeteam.store import local_facts
from claudeteam.util import pop_bool_flag, usage_error


USAGE = (
    "usage: claudeteam send <to> <from> <message> [priority] "
    "[--no-inject]"
)


def main(argv: list[str]) -> int:
    rest = list(argv)
    no_inject = pop_bool_flag(rest, "--no-inject")
    if len(rest) < 3:
        return usage_error(USAGE)
    to, frm, message = rest[0], rest[1], rest[2]
    priority = rest[3] if len(rest) > 3 else "中"
    local_facts.touch_heartbeat(frm)
    local_id = local_facts.append_message(to, frm, message, priority=priority)
    print(f"📥 inbox: {to} ← {frm}  [local_id={local_id}]")
    if no_inject:
        return 0
    # Best-effort tmux inject so the recipient's pane sees a nudge to
    # read inbox. Failures here (no session, no pane, unknown adapter)
    # don't fail the command — the inbox row is still the canonical
    # record the recipient will pick up next time they re-init or
    # /clear and re-read identity.
    try:
        session = config.session_name()
        target = tmux.Target(session, to)
        if not tmux.has_window(target):
            return 0
        adapter = adapter_for_agent(to)
        # Lazy worker only: pane exists as placeholder shell, CLI hasn't
        # spawned yet. Without wake_if_dormant the inject below would land
        # in the shell, not the CLI — agent never sees the message.
        # REGRESSION 2026-05-06 host_smoke §7: lazy worker_codex received
        # a manager dispatch but pane stayed at a bare shell prompt.
        # Non-lazy agents (typically manager + active workers) are
        # ALREADY started by `claudeteam up`; injecting straight in is
        # faster than the is_ready capture-pane round-trip and matches
        # the boss preference 2026-05-06: "send 主管时不需要等待他空闲,
        # 直接往 session 里面加告诉他就行了". Claude / Codex pane stash
        # injected text into the input buffer if mid-thought; it's read
        # on the next input-accept turn.
        cfg = config.agent_config(to) if to in config.agent_names() else {}
        if cfg.get("lazy") and not wake.is_ready(target, adapter):
            from claudeteam.runtime import tunables
            spawn_cmd = (f"{lifecycle.pane_env_prefix(to)} "
                         f"{adapter.spawn_cmd(to, config.agent_model(to))}")
            wake.wake_if_dormant(
                target, adapter,
                spawn_cmd=spawn_cmd,
                init_msg=_identity.init_prompt(to),
                timeout_s=float(tunables.tunable("wake.lazy_wake_timeout_s", 30.0)),
                on_woken=lambda: local_facts.upsert_status(
                    to, "进行中", "responding to first message"),
            )
        nudge = (f"📥 {frm} → {to}（{local_id}）。"
                 f"`claudeteam inbox {to}` → 处理 → "
                 f"`claudeteam read {local_id}` → 必要时 "
                 f"`claudeteam say {to} \"...\" --to user`。")
        tmux.inject(target, nudge, submit_keys=adapter.submit_keys())
    except Exception as e:
        print(f"  ⚠️ tmux inject best-effort failed for {to}: {e}")
    return 0
