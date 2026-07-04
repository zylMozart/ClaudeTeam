"""`claudeteam send <to> <from> <message> [priority] [--no-inject]`

Append a message to the local inbox AND poke the recipient's tmux
pane so they know to read it.

Previously inbox-only with the doc claim "only the Feishu
router can do tmux inject". That broke peer messaging end-to-end —
manager sending to worker_cc wrote a row, but worker_cc had no way
to know unless it polled: manager.send → worker_cc went into a
dead drop.

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
from claudeteam.util import (
    maybe_print_help, pop_bool_flag, reject_flag_as_agent, usage_error)


USAGE = (
    "usage: claudeteam send <to> <from> <message> [priority] "
    "[--no-inject]"
)


def main(argv: list[str]) -> int:
    if maybe_print_help(argv, USAGE):
        return 0
    rest = list(argv)
    no_inject = pop_bool_flag(rest, "--no-inject")
    if len(rest) < 3:
        return usage_error(USAGE)
    to, frm, message = rest[0], rest[1], rest[2]
    priority = rest[3] if len(rest) > 3 else "中"
    for name in (to, frm):
        if (rc := reject_flag_as_agent(name, USAGE)) is not None:
            return rc
    local_facts.touch_heartbeat(frm)
    local_id = local_facts.append_message(to, frm, message, priority=priority)
    print(f"📥 inbox: {to} ← {frm}  [local_id={local_id}]")
    if no_inject:
        return 0
    # ACP recipient: durably queue the nudge for the router's AcpHost —
    # works from ANY process (this command runs in the sender's shell,
    # not the router) and survives a router restart, unlike a pane poke.
    if config.agent_runner(to) == "acp":
        if local_facts.is_retired(to):
            print(f"  ⏸️  {to} 已停止 (fired); inbox row kept, not prompted")
            return 0
        from claudeteam.store import acp_queue
        nudge = (f"📥 {frm} → {to}（{local_id}）。"
                 f"`claudeteam inbox {to}` → 处理 → "
                 f"`claudeteam read {local_id}` → 必要时 "
                 f"`claudeteam say {to} \"...\" --to user`。")
        try:
            acp_queue.enqueue(to, nudge, sender=frm, local_id=local_id)
            print(f"  📮 queued for {to} (acp)")
        except OSError as e:
            print(f"  ⚠️ acp enqueue best-effort failed for {to}: {e}")
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
        # Retirement gate: a fired agent (status 已停止) keeps its inbox row
        # (written above — picked up on a future `hire`) but its pane is
        # never nudged/woken. Without this, a peer `send` to a fired agent
        # whose window still lingers would inject (and the lazy branch
        # below could even respawn its CLI), reviving a retired agent.
        if local_facts.is_retired(to):
            print(f"  ⏸️  {to} 已停止 (fired); inbox row kept, pane not nudged")
            return 0
        adapter = adapter_for_agent(to)
        # Lazy worker only: pane exists as placeholder shell, CLI hasn't
        # spawned yet. Without wake_if_dormant the inject below would land
        # in the shell, not the CLI — agent never sees the message.
        # Without it, a lazy worker that received a manager dispatch
        # would stay at a bare shell prompt.
        # Non-lazy agents (typically manager + active workers) are
        # ALREADY started by `claudeteam up`; injecting straight in is
        # faster than the is_ready capture-pane round-trip — there's no
        # need to wait for the manager to be idle, just add to the
        # session. Claude / Codex pane stash injected text into the
        # input buffer if mid-thought; it's read on the next
        # input-accept turn.
        cfg = config.agent_config(to) if to in config.agent_names() else {}
        if cfg.get("lazy") and not wake.is_ready(target, adapter):
            from claudeteam.runtime import tunables
            spawn_cmd = lifecycle.build_spawn_command(
                to, adapter, adapter.spawn_cmd(to, config.agent_model(to)))
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
