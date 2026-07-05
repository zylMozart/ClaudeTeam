"""Headless (no-tmux) mode — the path Windows native / minimal servers
take. ACP agents must provision and run without tmux; tmux-runner agents
must be refused loudly, not silently broken."""
from __future__ import annotations

from helpers import isolated_env, run_cli, tmux_patch
from claudeteam.runtime import lifecycle, paths
from claudeteam.store import local_facts


def _no_tmux():
    """Simulate a host without the tmux binary (the seam start/health use)."""
    return tmux_patch(available=lambda: False)


_MIXED = {"session": "HeadlessT", "agents": {
    "manager":     {"cli": "claude-code"},              # acp by default
    "worker_kimi": {"cli": "kimi-code"},                # tmux-bound
}}


def test_start_headless_provisions_acp_and_refuses_pane_bound():
    with isolated_env(team=_MIXED), _no_tmux():
        rc, out, err = run_cli(["start"])
        assert rc == 0
        assert "headless mode" in out
        # acp agent fully provisioned: identity + workspace + status
        assert (paths.agent_dir("manager") / "identity.md").exists()
        assert paths.agent_workspace("manager").is_dir()
        assert local_facts.get_status("manager")["status"] == "待命"
        # tmux-bound agent refused loudly, not provisioned
        assert "worker_kimi" in (out + err)
        assert local_facts.get_status("worker_kimi") is None


def test_start_headless_all_tmux_roster_is_an_error():
    team = {"agents": {"w": {"cli": "kimi-code"}}}
    with isolated_env(team=team), _no_tmux():
        rc, out, err = run_cli(["start"])
        assert rc == 1
        assert "tmux" in (out + err)


def test_provision_headless_is_idempotent():
    with isolated_env(team=_MIXED):
        assert lifecycle.provision_headless("manager") == lifecycle.READY
        assert lifecycle.provision_headless("manager") == lifecycle.READY


def test_health_headless_all_acp_is_yellow_not_red():
    team = {"agents": {"manager": {"cli": "claude-code"}}}
    with isolated_env(team=team, runtime_config={"chat_id": "oc_x"}), _no_tmux():
        rc, out, _ = run_cli(["health"])
        assert "headless mode" in out
        # yellow note, not the red "session not running" failure
        assert "not running (run" not in out


def test_health_headless_with_tmux_agents_is_red():
    with isolated_env(team=_MIXED, runtime_config={"chat_id": "oc_x"}), _no_tmux():
        rc, out, _ = run_cli(["health"])
        assert rc == 1
        assert "tmux-runner" in out
