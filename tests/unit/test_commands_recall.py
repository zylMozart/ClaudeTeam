"""Tests for `claudeteam recall <agent>` — memory inspection."""
from __future__ import annotations

import json

from helpers import isolated_env, run_cli
from claudeteam.store import memory


def test_recall_empty_memory_prints_friendly_message():
    with isolated_env():
        rc, out, _ = run_cli(["recall", "manager"])
        assert rc == 0
        assert "no memory entries" in out


def test_recall_lists_entries_oldest_first_with_kind_and_ref():
    """Bullets render `[timestamp] [kind] content (ref=X)`. Order is
    chronological (oldest first), matching memory.list_recent semantics."""
    with isolated_env():
        memory.append("worker_cc", "task_assigned", "fix login", ref="om_1")
        memory.append("worker_cc", "task_completed", "fix login", ref="om_1")
        rc, out, _ = run_cli(["recall", "worker_cc"])
        assert rc == 0
        assert "🧠 worker_cc: 2 entries" in out
        assert "[task_assigned] fix login  (ref=om_1)" in out
        assert "[task_completed] fix login  (ref=om_1)" in out
        # Order: assigned line appears before completed
        assert out.index("task_assigned") < out.index("task_completed")


def test_recall_limit_default_is_twenty():
    """When no --limit, show up to 20 entries (memory's default cap window)."""
    with isolated_env():
        for i in range(30):
            memory.append("w", "note", f"i={i}")
        rc, out, _ = run_cli(["recall", "w"])
        assert rc == 0
        # 20 newest, oldest-first → i=10 to i=29 in body
        assert "i=10" in out
        assert "i=29" in out
        assert "i=9" not in out


def test_recall_respects_explicit_limit():
    with isolated_env():
        for i in range(10):
            memory.append("w", "note", f"i={i}")
        rc, out, _ = run_cli(["recall", "w", "--limit", "3"])
        assert rc == 0
        # last 3 → i=7, 8, 9
        for i in (7, 8, 9):
            assert f"i={i}" in out
        for i in (0, 1, 2, 3, 4, 5, 6):
            assert f"i={i}" not in out


def test_recall_json_dumps_records_machine_readable():
    """--json emits the raw record list — for jq / smoke conductors."""
    with isolated_env():
        memory.append("w", "learning", "auth uses bcrypt")
        rc, out, _ = run_cli(["recall", "w", "--json"])
        assert rc == 0
        rows = json.loads(out)
        assert len(rows) == 1
        assert rows[0]["kind"] == "learning"
        assert rows[0]["content"] == "auth uses bcrypt"


def test_recall_invalid_limit_returns_error():
    rc, _, err = run_cli(["recall", "w", "--limit", "abc"])
    assert rc == 1
    assert "must be an integer" in err


# ── --kind filter (round-107) ───────────────────────────────────


def test_recall_kind_filter_only_returns_matching_entries():
    """Round-107: --kind narrows recall to one of memory.KNOWN_KINDS,
    so boss can scan a slice (`--kind decision`) without grep."""
    with isolated_env():
        memory.append("manager", "decision", "use bcrypt")
        memory.append("manager", "learning", "auth path is /auth/v2")
        memory.append("manager", "decision", "rotate keys monthly")
        memory.append("manager", "note", "stand-up at 10am")

        rc, out, _ = run_cli(["recall", "manager", "--kind", "decision"])
        assert rc == 0
        assert "filter kind=decision" in out
        assert "use bcrypt" in out
        assert "rotate keys monthly" in out
        # Non-matching kinds excluded
        assert "auth path" not in out
        assert "stand-up" not in out


def test_recall_kind_filter_empty_match_friendly_message():
    """Filter matches nothing: print empty-state line that mentions the
    filter so operator knows what was queried."""
    with isolated_env():
        memory.append("manager", "note", "only a note")
        rc, out, _ = run_cli(["recall", "manager", "--kind", "decision"])
        assert rc == 0
        assert "no memory entries (kind=decision)" in out


def test_recall_kind_unknown_warns_but_proceeds():
    """An unconventional kind filter (`fyi`) is allowed (someone might
    have written a `fyi` entry past the soft-warn gate in append) — but
    surfaces a stderr warning pointing at KNOWN_KINDS, so a typo of a
    real kind is obvious."""
    with isolated_env():
        memory.append("manager", "fyi", "non-canonical entry")  # also stderr-warns
        rc, out, err = run_cli(["recall", "manager", "--kind", "fyi"])
        assert rc == 0
        # Stderr surfaced the convention list
        assert "not in known kinds" in err
        # Result still found
        assert "non-canonical entry" in out


def test_recall_kind_filter_with_limit_trims_after_filter():
    """`--limit N` + `--kind K` should give N MATCHES, not N reads.
    Otherwise a hot agent with mostly notes would never surface its
    rare decisions."""
    with isolated_env():
        # 50 notes, 3 decisions (recent-most ordering)
        for i in range(50):
            memory.append("worker_cc", "note", f"note {i}")
        memory.append("worker_cc", "decision", "decision 1")
        memory.append("worker_cc", "decision", "decision 2")
        memory.append("worker_cc", "decision", "decision 3")
        rc, out, _ = run_cli(["recall", "worker_cc", "--kind", "decision",
                              "--limit", "5"])
        assert rc == 0
        for d in ("decision 1", "decision 2", "decision 3"):
            assert d in out
        # No notes leaked despite limit > matches
        assert "note 0" not in out
        assert "note 49" not in out


def test_recall_zero_limit_returns_error():
    rc, _, err = run_cli(["recall", "w", "--limit", "0"])
    assert rc == 1
    assert ">= 1" in err


def test_recall_zero_args_returns_usage():
    rc, _, err = run_cli(["recall"])
    assert rc == 1
    assert "usage:" in err


def test_recall_help_flag():
    rc, out, _ = run_cli(["recall", "--help"])
    assert rc == 0
    assert "usage: claudeteam recall" in out


def test_recall_help_lists_known_kinds():
    """Round-110: --help advertises memory.KNOWN_KINDS so operators
    don't have to grep the source to learn the convention."""
    rc, out, _ = run_cli(["recall", "--help"])
    for k in memory.KNOWN_KINDS:
        assert k in out
    # Make the "any string accepted" caveat visible too
    assert "stderr" in out.lower() or "nudge" in out.lower()


def test_recall_registered_in_cli():
    from claudeteam.cli import COMMANDS
    assert "recall" in COMMANDS
