"""Tests for `claudeteam remember` — agent durable memory entry point."""
from __future__ import annotations

from helpers import isolated_env, run_cli
from claudeteam.store import memory


def test_remember_appends_entry_with_kind_and_content():
    with isolated_env():
        rc, out, _ = run_cli(["remember", "manager", "learning",
                              "auth uses bcrypt"])
        assert rc == 0
        assert "🧠 remembered" in out
        assert "manager/learning" in out
        rows = memory.list_recent("manager")
        assert len(rows) == 1
        assert rows[0]["kind"] == "learning"
        assert rows[0]["content"] == "auth uses bcrypt"
        assert rows[0]["ref"] == ""


def test_remember_threads_ref_when_passed():
    """--ref ties the memory to an external artefact (message_id, task_id).
    Renders as `(ref=om_xx)` suffix in render_for_prompt."""
    with isolated_env():
        rc, _, _ = run_cli(["remember", "worker_cc", "task_assigned",
                            "fix login flow", "--ref", "om_42"])
        assert rc == 0
        rows = memory.list_recent("worker_cc")
        assert rows[0]["ref"] == "om_42"


def test_remember_joins_multi_word_content():
    """Convenience: callers can pass an unquoted message; everything after
    kind gets joined so the agent doesn't have to babysit shell quoting."""
    with isolated_env():
        rc, _, _ = run_cli(["remember", "worker_codex", "decision",
                            "use", "openai-fn", "calling", "for", "this"])
        assert rc == 0
        rows = memory.list_recent("worker_codex")
        assert rows[0]["content"] == "use openai-fn calling for this"


def test_remember_too_few_args_returns_usage_error():
    with isolated_env():
        rc, _, err = run_cli(["remember", "manager", "learning"])  # no content
        assert rc == 1
        assert "usage:" in err


def test_remember_help_flag():
    rc, out, _ = run_cli(["remember", "--help"])
    assert rc == 0
    assert "usage: claudeteam remember" in out


def test_remember_help_lists_known_kinds():
    """Round-110: --help advertises memory.KNOWN_KINDS so agents see
    the convention before writing their first entry, instead of
    discovering it via the stderr soft-warn after the fact."""
    from claudeteam.store import memory
    rc, out, _ = run_cli(["remember", "--help"])
    for k in memory.KNOWN_KINDS:
        assert k in out


def test_remember_registered_in_cli():
    """Round-87: top-level `claudeteam remember` must be in the COMMANDS
    registry; otherwise managers/workers calling from pane get
    `unknown command`."""
    from claudeteam.cli import COMMANDS
    assert "remember" in COMMANDS
