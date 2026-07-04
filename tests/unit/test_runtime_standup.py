"""Tests for runtime/standup.py — the periodic progress-report ticker."""
from __future__ import annotations

from helpers import env_patch, isolated_env
from claudeteam.runtime import standup
from claudeteam.store import acp_queue, local_facts
from claudeteam.util import now_ms


_TEAM = {"agents": {
    "manager":     {"cli": "claude-code"},          # acp by default
    "worker_kimi": {"cli": "kimi-code"},            # tmux
}}


def _iso():
    return isolated_env(team=_TEAM)


# ── team_active ───────────────────────────────────────────────────


def test_idle_team_is_not_active():
    with _iso():
        assert not standup.team_active(["manager", "worker_kimi"],
                                       window_ms=60_000)


def test_fresh_boss_message_makes_team_active():
    with _iso():
        local_facts.append_message("manager", "user", "去干活")
        assert standup.team_active(["manager"], window_ms=60_000)


def test_standups_own_rows_do_not_self_sustain():
    """The ticker's own queue rows / inbox rows must NOT count as activity,
    or the reports would never stop after the team goes idle."""
    with _iso():
        acp_queue.enqueue("manager", "[定时巡视] ...", sender=standup.SENDER)
        local_facts.append_message("manager", standup.SENDER, "巡视")
        assert not standup.team_active(["manager"], window_ms=60_000)


def test_stale_activity_outside_window_is_ignored():
    with _iso():
        local_facts.append_message("manager", "user", "old task")
        future = now_ms() + 120_000
        assert not standup.team_active(["manager"], window_ms=60_000,
                                       now=lambda: future)


def test_working_status_counts_as_active():
    with _iso():
        local_facts.upsert_status("worker_kimi", "进行中", "写周报")
        assert standup.team_active(["worker_kimi"], window_ms=60_000)


# ── tick ──────────────────────────────────────────────────────────


def test_tick_sends_report_request_when_active_and_due():
    with _iso():
        local_facts.append_message("manager", "user", "推进项目")
        assert standup.tick(log=lambda *a, **k: None)
        rows = acp_queue.rows("manager", state=acp_queue.PENDING)
        standup_rows = [r for r in rows if r["sender"] == standup.SENDER]
        assert len(standup_rows) == 1
        assert "巡视" in standup_rows[0]["text"]
        assert "claudeteam say" in standup_rows[0]["text"]
        # cadence clock recorded
        assert standup.last_report_at() > 0


def test_tick_respects_interval():
    with _iso():
        local_facts.append_message("manager", "user", "推进项目")
        assert standup.tick(log=lambda *a, **k: None)
        # immediately due again? no — interval not elapsed
        assert not standup.tick(log=lambda *a, **k: None)
        rows = [r for r in acp_queue.rows("manager")
                if r["sender"] == standup.SENDER]
        assert len(rows) == 1


def test_tick_silent_when_idle():
    with _iso():
        assert not standup.tick(log=lambda *a, **k: None)
        assert acp_queue.rows("manager") == []


def test_tick_disabled_via_env_override():
    with _iso():
        local_facts.append_message("manager", "user", "推进项目")
        with env_patch(CLAUDETEAM_STANDUP_ENABLED="false"):
            assert not standup.tick(log=lambda *a, **k: None)


def test_tick_noop_when_target_missing_from_roster():
    with isolated_env(team={"agents": {"worker_kimi": {"cli": "kimi-code"}}}):
        local_facts.append_message("worker_kimi", "user", "hi")
        assert not standup.tick(log=lambda *a, **k: None)


def test_retired_target_not_prompted():
    with _iso():
        local_facts.append_message("manager", "user", "推进项目")
        local_facts.upsert_status("manager", local_facts.RETIRED_STATUS, "fired")
        assert not standup.tick(log=lambda *a, **k: None)
        assert acp_queue.rows("manager") == []


def test_trigger_now_bypasses_interval_and_activity():
    with _iso():
        # idle team + no prior report — /standup still fires
        assert standup.trigger_now(log=lambda *a, **k: None)
        rows = [r for r in acp_queue.rows("manager")
                if r["sender"] == standup.SENDER]
        assert len(rows) == 1


def test_report_prompt_lists_teammates_not_target():
    text = standup.report_prompt(["worker_a", "worker_b"])
    assert "worker_a" in text and "worker_b" in text
    assert "不要编造进度" in text
