"""Tests for `claudeteam reidentify <agent>`."""
from __future__ import annotations

from helpers import isolated_env, run_cli, tmux_patch


_TEAM = {"session": "S", "agents": {"manager": {"runner": "tmux"},
                                    "worker_cc": {"runner": "tmux"}}}


def test_reidentify_zero_args_returns_one():
    rc, _, err = run_cli(["reidentify"])
    assert rc == 1
    assert "usage:" in err


def test_reidentify_unknown_agent_returns_one():
    with isolated_env(team=_TEAM):
        rc, _, err = run_cli(["reidentify", "ghost"])
        assert rc == 1
        assert "unknown agent" in err


def test_reidentify_session_down_returns_one():
    with isolated_env(team=_TEAM), tmux_patch(has_session=lambda s: False):
        rc, _, err = run_cli(["reidentify", "manager"])
        assert rc == 1
        assert "tmux session" in err and "not running" in err


def test_reidentify_no_pane_returns_one():
    with isolated_env(team=_TEAM), tmux_patch(
            has_session=lambda s: True,
            has_window=lambda t: False):
        rc, _, err = run_cli(["reidentify", "manager"])
        assert rc == 1
        assert "no pane" in err


def test_reidentify_injects_init_prompt_into_existing_pane():
    captured = {}

    def fake_inject(target, text, **kw):
        captured["target"] = str(target)
        captured["text"] = text
        return True

    with isolated_env(team=_TEAM), tmux_patch(
            has_session=lambda s: True,
            has_window=lambda t: True,
            inject=fake_inject):
        rc, out, _ = run_cli(["reidentify", "manager"])
        assert rc == 0
        assert captured["target"] == "S:manager"
        # init_prompt body: "You are manager. Read agents/manager/identity.md"
        assert "You are manager" in captured["text"]
        assert "agents/manager/identity.md" in captured["text"]
        assert "✅" in out


# ── --all flag ───────────────────────────────────────────────────


def test_reidentify_all_injects_into_every_agent():
    """`reidentify --all` walks team.json and injects into each pane."""
    injects = []

    def fake_inject(target, text, **kw):
        injects.append({"target": str(target), "text": text})
        return True

    with isolated_env(team=_TEAM), tmux_patch(
            has_session=lambda s: True,
            has_window=lambda t: True,
            inject=fake_inject):
        rc, out, _ = run_cli(["reidentify", "--all"])
    assert rc == 0
    targets = sorted(i["target"] for i in injects)
    assert targets == ["S:manager", "S:worker_cc"]
    assert "reidentified 2/2" in out


def test_reidentify_all_skips_agents_without_pane():
    """Lazy / fired agents have no live pane — skip them, don't fail."""
    # Only "manager" pane exists; worker_cc has no window yet
    panes = {"manager"}

    def fake_has_window(target):
        return target.window in panes

    def fake_inject(target, text, **kw):
        return True

    with isolated_env(team=_TEAM), tmux_patch(
            has_session=lambda s: True,
            has_window=fake_has_window,
            inject=fake_inject):
        rc, out, _ = run_cli(["reidentify", "--all"])
    # 1 of 2 succeeded → rc=1 (caller decides; partial-success isn't full)
    assert rc == 1
    assert "reidentified 1/2" in out
    assert "no pane" in out  # the skipped one logs


def test_reidentify_all_session_down_returns_one():
    """No tmux session → one error, no per-agent walk."""
    with isolated_env(team=_TEAM), tmux_patch(has_session=lambda s: False):
        rc, _, err = run_cli(["reidentify", "--all"])
    assert rc == 1
    assert "not running" in err


def test_reidentify_all_empty_team_returns_one():
    """Empty agents dict → return 1 with explicit error (don't silently
    succeed with 0/0)."""
    with isolated_env(team={"session": "S", "agents": {}}), tmux_patch(
            has_session=lambda s: True):
        rc, _, err = run_cli(["reidentify", "--all"])
    assert rc == 1
    assert "no agents" in err


# ── --print (render without a live team) + --help + error wording ─────────────


def test_reidentify_print_renders_identity_without_live_team():
    """`--print` renders the identity to stdout with NO tmux session — so a
    freshly-edited config / playbook can be verified without `claudeteam up`."""
    team = {"session": "S", "agents": {
        "worker_cc": {"cli": "claude-code", "model": "sonnet", "role": "PRINTROLE"}}}
    # tmux deliberately NOT patched — --print must not touch it
    with isolated_env(team=team):
        rc, out, err = run_cli(["reidentify", "worker_cc", "--print"])
    assert rc == 0, err
    assert "PRINTROLE" in out          # the rendered identity is printed
    assert "team worker" in out         # incl the team-protocol body, not just the role


def test_reidentify_unknown_agent_names_claudeteam_toml():
    """Config is claudeteam.toml — the error must not name the legacy
    team.json (which no longer exists)."""
    with isolated_env(team=_TEAM):
        rc, _, err = run_cli(["reidentify", "ghost"])
    assert rc == 1
    assert "claudeteam.toml" in err and "team.json" not in err


def test_subcommands_print_help_not_execute():
    """`claudeteam <cmd> --help` must PRINT usage and exit 0, not treat --help
    as a positional and run the command (regression for the missing
    maybe_print_help gate)."""
    for cmd in ("reidentify", "hire", "fire", "team", "say", "read",
                "restart", "workspace"):
        rc, out, err = run_cli([cmd, "--help"])
        assert rc == 0, f"{cmd} --help → rc={rc}, err={err}"
        assert "usage" in (out + err).lower(), f"{cmd} --help printed no usage"
