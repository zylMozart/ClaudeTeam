"""Periodic progress reports: while the team is working, nudge the
manager every N minutes to inspect everyone and report to the boss.

Why: a long-running task can go quiet for an hour — workers grinding,
boss in the dark, drift undetected. This ticker keeps the loop closed:
every `standup.interval_minutes` (default 10) DURING ACTIVE WORK, the
manager gets a 巡视 prompt telling it to check each agent's progress and
post one consolidated report to the group. When the team is idle, no
reports (a silent chat at 3am is a feature).

"Active" is judged from durable signals, deliberately NOT heartbeats —
the standup turn itself bumps the manager's heartbeat, which would make
the ticker self-sustaining forever. Instead:

  1. a fresh inbox message from anyone except the ticker itself
     (boss messages, peer sends) within the activity window, or
  2. a fresh ACP queue row enqueued by someone other than the ticker, or
  3. an agent whose status row currently claims 进行中 (self-reported
     "working") updated within the window.

State: `state/standup.json` (last_report_at_ms) — survives router
restarts so a respawn doesn't double-fire.

Config (claudeteam.toml, env-overridable via CLAUDETEAM_STANDUP_*):

    [standup]
    enabled = true            # default true
    interval_minutes = 10     # cadence while active (boss asked 8–10)
    activity_window_minutes = 45   # how far back "active" looks
    target = "manager"        # who runs the 巡视 and reports

The ticker runs as a daemon thread inside `claudeteam router`, next to
the AcpHost. `/standup` in chat triggers one immediately.
"""
from __future__ import annotations

import threading
from typing import Callable

from claudeteam.runtime import config, paths
from claudeteam.store import acp_queue, local_facts
from claudeteam.util import now_ms, read_json, write_json


SENDER = "standup"   # marks the ticker's own rows so they don't count as activity


def _state_file():
    return paths.state_file("standup.json")


def last_report_at() -> int:
    return int(read_json(_state_file(), {}).get("last_report_at_ms", 0))


def _mark_reported() -> None:
    write_json(_state_file(), {"last_report_at_ms": now_ms()})


def report_prompt(agents: list[str]) -> str:
    """The 巡视 instruction injected into the target agent. Kept concrete
    and step-by-step so weaker models execute it reliably."""
    others = "、".join(a for a in agents) or "(none)"
    return (
        "[定时巡视·standup] 现在做一轮团队进度巡视并向老板汇报。步骤：\n"
        f"1. `claudeteam team` 看全员状态（团队成员：{others}）。\n"
        "2. 对每个显示在忙/进行中的 agent 跑 `claudeteam peek <name> 40`，"
        "看它实际在做什么、有没有卡住。\n"
        "3. 如在用任务追踪，`claudeteam task list` 对照目标。\n"
        "4. 汇总成一条简洁汇报发到群里：`claudeteam say <你的名字> \"...\" --to user`，"
        "内容包括：每人正在做什么（一行一个）、整体进展到哪一步、"
        "当前卡点（没有就说无）、接下来这个周期你准备推进什么。\n"
        "如果所有人都空闲且没有待办任务，只发一句话说明团队空闲即可，不要编造进度。"
    )


def team_active(agents: list[str], *, window_ms: int,
                now: Callable[[], int] = now_ms) -> bool:
    """Durable-signal activity check — see module docstring for why
    heartbeats are excluded."""
    t = now()
    for agent in agents:
        for m in local_facts.list_messages(agent):
            if m.get("from") != SENDER and t - int(m.get("created_at", 0)) < window_ms:
                return True
        try:
            for r in acp_queue.rows(agent):
                if r.get("sender") != SENDER and t - int(r.get("enq_at", 0)) < window_ms:
                    return True
        except OSError:
            pass
        st = local_facts.get_status(agent)
        if (st and st.get("status") == "进行中"
                and t - int(st.get("updated_at", 0)) < window_ms):
            return True
    return False


def deliver_report_request(target: str, agents: list[str], *,
                           log: Callable = print) -> bool:
    """Hand the 巡视 prompt to the target agent, runner-aware. Returns
    True iff it was queued/injected."""
    prompt = report_prompt([a for a in agents if a != target])
    if local_facts.is_retired(target):
        log(f"  ⏭  standup: {target} is retired; skipping")
        return False
    if config.agent_runner(target) == "acp":
        try:
            acp_queue.enqueue(target, prompt, sender=SENDER)
            return True
        except OSError as e:
            log(f"  ⚠️ standup enqueue failed: {e}")
            return False
    # tmux target: best-effort pane inject (same posture as deliver)
    from claudeteam.agents import adapter_for_agent
    from claudeteam.runtime import tmux
    try:
        adapter = adapter_for_agent(target)
        tgt = tmux.Target(config.session_name(), target)
        return bool(tmux.inject(tgt, prompt, submit_keys=adapter.submit_keys()))
    except Exception as e:
        log(f"  ⚠️ standup inject failed for {target}: {e}")
        return False


def tick(*, now: Callable[[], int] = now_ms, log: Callable = print) -> bool:
    """One scheduler check. Returns True iff a report request was sent.
    Pure-ish (all config re-read live) so the thread loop stays dumb and
    tests drive this directly."""
    from claudeteam.runtime import tunables
    if not bool(tunables.tunable("standup.enabled", True)):
        return False
    interval_ms = int(float(tunables.tunable("standup.interval_minutes", 10)) * 60_000)
    window_ms = int(float(tunables.tunable("standup.activity_window_minutes", 45)) * 60_000)
    target = str(tunables.tunable("standup.target", "manager"))
    agents = config.agent_names()
    if target not in agents:
        return False
    if now() - last_report_at() < interval_ms:
        return False
    if not team_active(agents, window_ms=window_ms, now=now):
        return False
    if not deliver_report_request(target, agents, log=log):
        return False
    _mark_reported()
    log(f"  📣 standup: 巡视请求已发给 {target}")
    return True


def trigger_now(*, log: Callable = print) -> bool:
    """`/standup` slash: fire immediately regardless of interval/activity,
    and reset the cadence clock."""
    from claudeteam.runtime import tunables
    target = str(tunables.tunable("standup.target", "manager"))
    agents = config.agent_names()
    if target not in agents:
        log(f"  ⚠️ standup target {target!r} not in roster")
        return False
    if not deliver_report_request(target, agents, log=log):
        return False
    _mark_reported()
    return True


class StandupTicker:
    """Daemon thread wrapper around tick(). Lives in the router."""

    def __init__(self, *, check_s: float = 30.0, log: Callable = print):
        self.check_s = check_s
        self.log = log
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None

    def start(self) -> None:
        self._thread = threading.Thread(target=self._loop, daemon=True,
                                        name="standup-ticker")
        self._thread.start()

    def _loop(self) -> None:
        while not self._stop.wait(self.check_s):
            try:
                tick(log=self.log)
            except Exception as e:  # scheduler must survive any bad state
                self.log(f"  ⚠️ standup tick error: {e}")

    def stop(self) -> None:
        self._stop.set()
        if self._thread is not None:
            self._thread.join(timeout=2)
