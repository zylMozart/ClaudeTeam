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

from helpers import attr_patch, env_patch, isolated_env, run_cli
from claudeteam.commands.router import (
    _build_subscribe_cmd,
    _load_seen_msg_ids,
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


def _patch_platform(name: str):
    """Force platform.system() to return `name` so tests are deterministic
    across the runner's OS. macOS dev laptop and Linux CI box would
    otherwise see different defaults (Darwin → 120, else → 600)."""
    import platform
    return attr_patch(platform, system=lambda: name)


def test_stale_threshold_default_linux_is_600s():
    """Linux WebSocket is stable; default stays 600s. Calibrated value:
    1200 too lax / 180 too tight (see commit history)."""
    with isolated_env(), env_patch(CLAUDETEAM_ROUTER_STALE_S=None), _patch_platform("Linux"):
        assert _stale_event_threshold_s() == 600.0


def test_stale_threshold_default_darwin_is_120s():
    """macOS lark-cli 1.0.23 WebSocket silently drops without reconnect
    (verified 2026-05-09 host smoke). Tighter default lets self-restart
    + catchup recover in ~2 min instead of ~10."""
    with isolated_env(), env_patch(CLAUDETEAM_ROUTER_STALE_S=None), _patch_platform("Darwin"):
        assert _stale_event_threshold_s() == 120.0


def test_stale_threshold_picks_up_env_override():
    """Env override beats platform default — operators can tune."""
    with isolated_env(), env_patch(CLAUDETEAM_ROUTER_STALE_S="60"), _patch_platform("Darwin"):
        assert _stale_event_threshold_s() == 60.0


def test_stale_threshold_falls_back_to_default_on_garbage():
    """Misconfigured env (`CLAUDETEAM_ROUTER_STALE_S=potato`) should fall
    back to platform default rather than raise."""
    with isolated_env(), env_patch(CLAUDETEAM_ROUTER_STALE_S="potato"), _patch_platform("Linux"):
        assert _stale_event_threshold_s() == 600.0


def test_stale_threshold_ignores_zero_or_negative():
    with isolated_env(), env_patch(CLAUDETEAM_ROUTER_STALE_S="0"), _patch_platform("Linux"):
        assert _stale_event_threshold_s() == 600.0
    with isolated_env(), env_patch(CLAUDETEAM_ROUTER_STALE_S="-5"), _patch_platform("Darwin"):
        assert _stale_event_threshold_s() == 120.0


def test_make_on_progress_refreshes_timestamp_on_each_event():
    """Every successful (non-DROP) event should bump last_event_at[0]
    so the watchdog's stale check sees fresh activity. DROP events don't
    flow through process_lines' on_progress, so they don't refresh."""
    from types import SimpleNamespace
    with isolated_env():
        last_event_at = [0.0]
        cb = _make_on_progress(last_event_at)
        # Mock decision (only attribute used is by record_decision; we patch
        # catchup.record_decision to a no-op so we don't need full Decision).
        from claudeteam.feishu import catchup
        real_record = catchup.record_decision
        catchup.record_decision = lambda d: None
        try:
            before = last_event_at[0]
            cb(SimpleNamespace(msg_id="om_x"), object())
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
    try:
        # env override beats toml + module default in tunable() — speeds
        # up the loop without depending on whatever sits in the user's
        # claudeteam.toml.
        with env_patch(CLAUDETEAM_ROUTER_STALE_S="0.1",
                       CLAUDETEAM_ROUTER_SUBSCRIBE_WATCHDOG_PERIOD_S="0.05"):
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
    try:
        # Stale threshold high so we know the trigger was the dead child;
        # subscribe_watchdog_period_s low so the loop iterates fast.
        with env_patch(CLAUDETEAM_ROUTER_STALE_S="3600",
                       CLAUDETEAM_ROUTER_SUBSCRIBE_WATCHDOG_PERIOD_S="0.05"):
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


# ── persisted dedup set (state/router.seen) ──────────────────────


def test_load_seen_returns_empty_when_file_missing():
    with isolated_env():
        assert _load_seen_msg_ids() == set()


def test_load_seen_reads_one_msg_id_per_line():
    from claudeteam.runtime import paths
    with isolated_env():
        paths.ensure_state_dir()
        paths.router_seen_file().write_text("om_a\nom_b\nom_c\n")
        assert _load_seen_msg_ids() == {"om_a", "om_b", "om_c"}


def test_load_seen_skips_blank_lines():
    from claudeteam.runtime import paths
    with isolated_env():
        paths.ensure_state_dir()
        paths.router_seen_file().write_text("om_a\n\nom_b\n   \n")
        assert _load_seen_msg_ids() == {"om_a", "om_b"}


def test_load_seen_truncates_huge_file_to_recent_window():
    """Bound the file size — long-running deploy can't grow seen.json
    indefinitely. Truncate to the last `router.seen_max_lines` on load.
    Use a tiny override (50) via env so the test stays fast without
    materialising a 5000-line file."""
    from claudeteam.runtime import paths
    cap = 50
    with isolated_env(), env_patch(CLAUDETEAM_ROUTER_SEEN_MAX_LINES=str(cap)):
        paths.ensure_state_dir()
        # Write more than the cap; oldest should be dropped.
        ids = [f"om_{i}" for i in range(cap + 200)]
        paths.router_seen_file().write_text("\n".join(ids) + "\n")
        loaded = _load_seen_msg_ids()
        assert len(loaded) == cap
        # Oldest dropped, newest kept
        assert "om_0" not in loaded
        assert f"om_{cap + 199}" in loaded
        # File on disk also truncated for next boot
        on_disk = paths.router_seen_file().read_text().strip().splitlines()
        assert len(on_disk) == cap


def test_on_progress_appends_msg_id_to_seen_file():
    """REGRESSION: 2026-05-06 host_smoke caught manager's own /tmux
    manager card forwarded into manager inbox every ~3.5min as router
    self-restarted. Root cause: seen_msg_ids was an in-memory set, not
    persisted, so catchup replay after restart re-applied messages.
    Now: each on_progress fires append-to-file."""
    from types import SimpleNamespace
    from claudeteam.runtime import paths
    with isolated_env():
        last_event_at = [0.0]
        cb = _make_on_progress(last_event_at)
        # Mock the catchup.record_decision side effect
        from claudeteam.feishu import catchup
        real_record = catchup.record_decision
        catchup.record_decision = lambda d: None
        try:
            cb(SimpleNamespace(msg_id="om_first"), object())
            cb(SimpleNamespace(msg_id="om_second"), object())
            cb(SimpleNamespace(msg_id=""), object())  # blank id is skipped
        finally:
            catchup.record_decision = real_record
        contents = paths.router_seen_file().read_text()
        assert "om_first" in contents
        assert "om_second" in contents
        # Empty id didn't add a blank line
        assert _load_seen_msg_ids() == {"om_first", "om_second"}


def test_seen_persists_across_simulated_restart():
    """Two consecutive _make_on_progress sessions sharing the same
    state dir: second session's _load_seen_msg_ids must see what the
    first session wrote."""
    from types import SimpleNamespace
    with isolated_env():
        from claudeteam.feishu import catchup
        real_record = catchup.record_decision
        catchup.record_decision = lambda d: None
        try:
            cb1 = _make_on_progress([0.0])
            cb1(SimpleNamespace(msg_id="om_X"), object())
        finally:
            catchup.record_decision = real_record
        # Simulate restart: load again
        seen = _load_seen_msg_ids()
        assert "om_X" in seen
