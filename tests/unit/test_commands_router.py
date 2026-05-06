"""Tests for `claudeteam router` daemon entry.

The Popen + signal-handler + endless-loop machinery in main() can't be
sanely unit-tested — it's plumbing around process_lines (separately
covered by test_feishu_subscribe + the in-process integration suite).
What CAN and SHOULD be tested:
  - _build_subscribe_cmd: the argv we hand to lark-cli
  - main() early-validation paths: missing chat_id, empty team,
    pidlock already held — all should exit non-zero with a clear
    stderr message before any subprocess is spawned.
"""
from __future__ import annotations

from helpers import env_patch, isolated_env, run_cli
from claudeteam.commands.router import (
    _build_subscribe_cmd,
    _make_on_progress,
    _stale_event_threshold_s,
    _watch_subscribe_health,
)


# Stub `resolve_prefix` so test argv doesn't depend on whether lark-cli
# is on the test runner's PATH. Real production resolution is tested in
# tests/unit/test_feishu_lark.py.
_STUB_PREFIX = lambda: ["FAKE-LARK-CLI"]


# ── _build_subscribe_cmd ──────────────────────────────────────────


def test_build_cmd_with_profile_inserts_profile_flag():
    cmd = _build_subscribe_cmd("test-live-a", resolve_prefix=_STUB_PREFIX)
    assert cmd[0] == "FAKE-LARK-CLI"
    assert "--profile" in cmd and "test-live-a" in cmd
    # --profile must come BEFORE the "event" subcommand (lark-cli
    # parses global flags before subcommand args)
    profile_idx = cmd.index("--profile")
    event_idx = cmd.index("event")
    assert profile_idx < event_idx


def test_build_cmd_uses_lark_resolve_cli_prefix():
    """REGRESSION (R139): subscribe argv must come from
    `lark.resolve_cli_prefix` (direct binary preferred). Hardcoded
    `npx @larksuite/cli` paid the package-lookup overhead on every
    router restart for nothing — R86's direct-binary work had been
    saving ~250-500 ms per one-shot call but missed this hot path."""
    from claudeteam.feishu import lark
    cmd = _build_subscribe_cmd("")
    expected = lark.resolve_cli_prefix()
    assert cmd[:len(expected)] == expected


def test_build_cmd_without_profile_omits_profile_flag():
    """No profile passed → no --profile in the argv (lark-cli falls back
    to its default profile)."""
    cmd = _build_subscribe_cmd("", resolve_prefix=_STUB_PREFIX)
    assert "--profile" not in cmd


def test_build_cmd_filters_to_im_message_receive():
    """Only inbound text-style chat events; lark-cli has many other event
    types we don't want firing the router."""
    cmd = _build_subscribe_cmd("", resolve_prefix=_STUB_PREFIX)
    assert "--event-types" in cmd
    et_idx = cmd.index("--event-types")
    assert cmd[et_idx + 1] == "im.message.receive_v1"


def test_build_cmd_uses_compact_quiet_bot_identity():
    """REGRESSION: --compact gets the JSON shape we parse; --quiet
    drops banner noise; --as bot uses the app's im:message scope
    rather than user OAuth (which expires)."""
    cmd = _build_subscribe_cmd("", resolve_prefix=_STUB_PREFIX)
    for flag in ("--compact", "--quiet"):
        assert flag in cmd, f"missing {flag}"
    as_idx = cmd.index("--as")
    assert cmd[as_idx + 1] == "bot"


def test_build_cmd_does_NOT_use_force_anymore():
    """REGRESSION (round-57): --force is "UNSAFE: server randomly splits
    events across connections, each instance only receives a subset"
    per lark-cli 1.0.21 docs. Was almost certainly a contributor to
    the silent event loss the catchup fix (round-56) papered over.
    The single-instance lock at ~/.lark-cli/locks/subscribe_<app>.lock
    is fcntl-advisory and auto-releases on process exit; claudeteam's
    own pidlock keeps us at one router at a time so collision is
    impossible in practice."""
    cmd = _build_subscribe_cmd("", resolve_prefix=_STUB_PREFIX)
    assert "--force" not in cmd, (
        "--force re-introduced; will cause silent event sharding")


# ── main() early validations ─────────────────────────────────────


def test_main_returns_one_when_chat_id_missing():
    """Empty chat_id in runtime_config → main exits before spawning
    lark-cli with a clear error."""
    team = {"agents": {"manager": {"cli": "claude-code"}}}
    rc_cfg = {"chat_id": "", "lark_profile": "test"}  # explicit empty
    with isolated_env(team=team, runtime_config=rc_cfg):
        rc, _, err = run_cli(["router"])
    assert rc == 1
    assert "chat_id" in err
    assert "runtime_config.json" in err


def test_main_returns_one_when_team_has_no_agents():
    """An empty team.json `agents` map means there's nothing to route
    TO — the daemon would just drop everything."""
    team = {"agents": {}}
    rc_cfg = {"chat_id": "oc_x", "lark_profile": "test"}
    with isolated_env(team=team, runtime_config=rc_cfg):
        rc, _, err = run_cli(["router"])
    assert rc == 1
    assert "no agents" in err


# ── help ────────────────────────────────────────────────────────


def test_main_help_returns_zero():
    rc, out, _ = run_cli(["router", "--help"])
    assert rc == 0
    assert "usage: claudeteam router" in out


# ── stale-event self-restart ──────────────────────────────────────


def test_stale_threshold_default_is_180s():
    """Default chosen after 2026-05-06 host_smoke caught lark WebSocket
    silently stalling within minutes of router boot. Earlier 1200s left
    user messages unprocessed for 7+min before self-heal."""
    with env_patch(CLAUDETEAM_ROUTER_STALE_S=None):
        assert _stale_event_threshold_s() == 180.0


def test_stale_threshold_picks_up_env_override():
    with env_patch(CLAUDETEAM_ROUTER_STALE_S="60"):
        assert _stale_event_threshold_s() == 60.0


def test_stale_threshold_falls_back_to_default_on_garbage():
    """Misconfigured env (`CLAUDETEAM_ROUTER_STALE_S=potato`) should fall
    back to default rather than raise."""
    with env_patch(CLAUDETEAM_ROUTER_STALE_S="potato"):
        assert _stale_event_threshold_s() == 180.0


def test_stale_threshold_ignores_zero_or_negative():
    with env_patch(CLAUDETEAM_ROUTER_STALE_S="0"):
        assert _stale_event_threshold_s() == 180.0
    with env_patch(CLAUDETEAM_ROUTER_STALE_S="-5"):
        assert _stale_event_threshold_s() == 180.0


def test_make_on_progress_refreshes_timestamp_on_each_event():
    """Every successful (non-DROP) event should bump last_event_at[0]
    so the watchdog's stale check sees fresh activity. DROP events don't
    flow through process_lines' on_progress, so they don't refresh."""
    last_event_at = [0.0]
    cb = _make_on_progress(last_event_at)
    # Mock decision (only attribute used is by record_decision; we patch
    # catchup.record_decision to a no-op so we don't need full Decision).
    from claudeteam.feishu import catchup
    real_record = catchup.record_decision
    catchup.record_decision = lambda d: None
    try:
        import time
        before = last_event_at[0]
        cb(decision=object(), stats=object())
        after = last_event_at[0]
    finally:
        catchup.record_decision = real_record
    # time.monotonic always > 0 since boot; before was 0.0
    assert after > before


def test_watch_subscribe_health_self_terminates_on_stale_events():
    """Subscribe child alive but no events for > threshold → SIGTERM
    self. REGRESSION: 2026-05-06 host_smoke caught lark WebSocket
    silently stalling, router process appeared healthy in `ps` but
    user messages went unprocessed for 7+ min."""
    import threading, signal, os
    from claudeteam.commands import router as _r

    class FakeProc:
        def __init__(self): self.returncode = None
        def poll(self): return None  # never exits

    sigterms = []
    real_kill = os.kill
    os.kill = lambda pid, sig: sigterms.append((pid, sig))
    real_period = _r._SUBSCRIBE_WATCHDOG_PERIOD_S
    _r._SUBSCRIBE_WATCHDOG_PERIOD_S = 0.05  # speed up loop for test
    try:
        with env_patch(CLAUDETEAM_ROUTER_STALE_S="0.1"):
            stop_event = threading.Event()
            # last_event_at far in the past → stale
            last_event_at = [0.0]
            t = threading.Thread(
                target=_watch_subscribe_health,
                args=(FakeProc(), stop_event, last_event_at),
                daemon=True,
            )
            t.start()
            t.join(timeout=2.0)
        assert sigterms, "watchdog thread didn't SIGTERM on stale events"
        assert sigterms[0][1] == signal.SIGTERM
    finally:
        os.kill = real_kill
        _r._SUBSCRIBE_WATCHDOG_PERIOD_S = real_period


def test_watch_subscribe_health_self_terminates_on_child_exit():
    """Subscribe child exits (non-stale-events path). Coverage for the
    pre-existing fail mode (R52 / Round B Smoke regression): npm-exec
    parent stays alive holding stdout open, lark-cli grandchild dies."""
    import threading, signal, os, time
    from claudeteam.commands import router as _r

    class FakeProc:
        def __init__(self): self.returncode = 137  # SIGKILL'd
        def poll(self): return self.returncode

    sigterms = []
    real_kill = os.kill
    os.kill = lambda pid, sig: sigterms.append((pid, sig))
    real_period = _r._SUBSCRIBE_WATCHDOG_PERIOD_S
    _r._SUBSCRIBE_WATCHDOG_PERIOD_S = 0.05
    try:
        # Stale threshold high so we know the trigger was the dead child
        with env_patch(CLAUDETEAM_ROUTER_STALE_S="3600"):
            stop_event = threading.Event()
            last_event_at = [time.monotonic()]  # fresh
            t = threading.Thread(
                target=_watch_subscribe_health,
                args=(FakeProc(), stop_event, last_event_at),
                daemon=True,
            )
            t.start()
            t.join(timeout=1.0)
        assert sigterms, "watchdog thread didn't SIGTERM on child exit"
        assert sigterms[0][1] == signal.SIGTERM
    finally:
        os.kill = real_kill
        _r._SUBSCRIBE_WATCHDOG_PERIOD_S = real_period
