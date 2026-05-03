"""Tests for store/memory.py — per-agent durable memory."""
from __future__ import annotations

from helpers import isolated_env
from claudeteam.store import memory


def test_append_and_list_roundtrip():
    with isolated_env():
        memory.append("manager", "task_assigned", "fix login bug", ref="om_1")
        memory.append("manager", "task_completed", "fix login bug", ref="om_1")
        rows = memory.list_recent("manager")
        assert len(rows) == 2
        assert rows[0]["kind"] == "task_assigned"
        assert rows[0]["content"] == "fix login bug"
        assert rows[0]["ref"] == "om_1"
        assert rows[1]["kind"] == "task_completed"
        # created_at populated and roughly monotonic
        assert rows[0]["created_at"] <= rows[1]["created_at"]


def test_list_recent_returns_oldest_first():
    """Order matters for prompt injection — the agent should read
    chronologically, oldest context first, so it ends with the latest
    state in working memory."""
    with isolated_env():
        for i in range(5):
            memory.append("worker", "note", f"step {i}")
        rows = memory.list_recent("worker")
        assert [r["content"] for r in rows] == [f"step {i}" for i in range(5)]


def test_list_recent_respects_limit():
    with isolated_env():
        for i in range(15):
            memory.append("worker", "note", f"item {i}")
        rows = memory.list_recent("worker", limit=5)
        # last 5 (oldest-first within the window)
        assert [r["content"] for r in rows] == [f"item {i}" for i in range(10, 15)]


def test_list_recent_empty_when_agent_unknown():
    with isolated_env():
        assert memory.list_recent("nobody") == []


def test_append_isolates_per_agent():
    """Two agents writing concurrently MUST NOT interleave; verify by
    cross-reading after sequential appends."""
    with isolated_env():
        memory.append("manager", "note", "manager note")
        memory.append("worker", "note", "worker note")
        m_rows = memory.list_recent("manager")
        w_rows = memory.list_recent("worker")
        assert len(m_rows) == 1 and m_rows[0]["content"] == "manager note"
        assert len(w_rows) == 1 and w_rows[0]["content"] == "worker note"


def test_append_caps_at_max_per_agent():
    """Memory growth is bounded — the oldest entries get dropped past
    the cap to keep the file small enough to inject into a prompt."""
    with isolated_env():
        for i in range(memory._MAX_PER_AGENT + 50):
            memory.append("worker", "note", f"i={i}")
        rows = memory.list_recent("worker", limit=memory._MAX_PER_AGENT * 2)
        assert len(rows) == memory._MAX_PER_AGENT
        # Oldest dropped → first surviving is i=50
        assert rows[0]["content"] == "i=50"
        assert rows[-1]["content"] == f"i={memory._MAX_PER_AGENT + 49}"


def test_append_tolerates_corrupt_pre_existing_lines():
    """If a previous crash left a half-written line, the next append
    should drop the bad line and keep going (not refuse all writes
    forever)."""
    with isolated_env():
        memory.append("worker", "note", "first")
        # Corrupt the file — append a half-written JSON line
        path = memory._memory_file("worker")
        path.write_text(path.read_text(encoding="utf-8") + '{"kind":"note","con',
                        encoding="utf-8")
        memory.append("worker", "note", "after corruption")
        rows = memory.list_recent("worker")
        # Bad line gone; both good entries kept
        assert [r["content"] for r in rows] == ["first", "after corruption"]


def test_render_for_prompt_empty_when_no_memory():
    with isolated_env():
        assert memory.render_for_prompt("nobody") == ""


def test_render_for_prompt_renders_bullets_with_ref():
    with isolated_env():
        memory.append("worker", "task_assigned", "fix login", ref="om_1")
        memory.append("worker", "learning", "auth uses bcrypt")
        rendered = memory.render_for_prompt("worker")
        assert "## 既往记忆" in rendered
        assert "[task_assigned] fix login (ref=om_1)" in rendered
        assert "[learning] auth uses bcrypt" in rendered
        # No ref → no `(ref=)` suffix
        assert "(ref=)" not in rendered


def test_clear_removes_file_and_returns_count():
    with isolated_env():
        memory.append("worker", "note", "a")
        memory.append("worker", "note", "b")
        n = memory.clear("worker")
        assert n == 2
        assert memory.list_recent("worker") == []
        # Idempotent: clearing twice returns 0
        assert memory.clear("worker") == 0


def test_all_agents_with_memory_lists_only_agents_that_wrote():
    with isolated_env():
        memory.append("manager", "note", "m")
        memory.append("worker_cc", "note", "w")
        agents = list(memory.all_agents_with_memory())
        assert sorted(agents) == ["manager", "worker_cc"]


# ── Round-111: clear_kind (scalpel inside the scalpel) ─────────


def test_clear_kind_drops_only_matching_entries():
    """Round-111: `clear_kind(agent, K)` removes only entries with
    kind == K, leaves others untouched."""
    with isolated_env():
        memory.append("manager", "decision", "use bcrypt")
        memory.append("manager", "blocker", "missing PAT")
        memory.append("manager", "decision", "rotate keys")
        memory.append("manager", "learning", "auth path /v2")
        n = memory.clear_kind("manager", "decision")
        assert n == 2
        rows = memory.list_recent("manager")
        kinds = [r["kind"] for r in rows]
        assert kinds == ["blocker", "learning"]


def test_clear_kind_returns_zero_when_no_match():
    """No matching kind → 0 dropped, no file mutation."""
    with isolated_env():
        memory.append("manager", "note", "only a note")
        n = memory.clear_kind("manager", "decision")
        assert n == 0
        assert len(memory.list_recent("manager")) == 1


def test_clear_kind_empty_agent_is_zero_no_op():
    with isolated_env():
        assert memory.clear_kind("nobody", "decision") == 0


def test_clear_kind_drops_all_unlinks_file():
    """When clear_kind drops every entry (only one kind in the file),
    the file is removed so list_recent treats the agent as fresh —
    matches `clear`'s 'empty memory == no file' invariant."""
    with isolated_env():
        memory.append("worker_cc", "blocker", "a")
        memory.append("worker_cc", "blocker", "b")
        n = memory.clear_kind("worker_cc", "blocker")
        assert n == 2
        # File gone
        assert not memory._memory_file("worker_cc").exists()
        assert memory.list_recent("worker_cc") == []


# ── Round-106: KNOWN_KINDS soft validation ──────────────────────


def test_known_kinds_covers_documented_vocabulary():
    """The 6 conventional kinds match what manager identity v2 +
    install-hooks `/remember` documentation teaches. Pin the set so a
    drift between code and docs gets caught."""
    assert set(memory.KNOWN_KINDS) == {
        "task_assigned", "task_completed", "learning",
        "blocker", "decision", "note",
    }


def test_append_warns_on_unknown_kind_but_still_writes():
    """Soft validation: unknown kind prints a stderr warning but the
    entry IS persisted. Free-form is sometimes the right call (a
    one-off `experiment_log`); we only nudge."""
    import contextlib, io
    with isolated_env():
        err = io.StringIO()
        with contextlib.redirect_stderr(err):
            memory.append("worker_cc", "fyi", "this is a note")
        # Warning visible
        assert "unknown kind 'fyi'" in err.getvalue()
        # Entry still written
        rows = memory.list_recent("worker_cc")
        assert len(rows) == 1
        assert rows[0]["kind"] == "fyi"


def test_append_silent_for_known_kinds():
    """No noise when the kind is in the convention — only unknown
    kinds nudge, otherwise stderr would flood on every memory write."""
    import contextlib, io
    with isolated_env():
        err = io.StringIO()
        with contextlib.redirect_stderr(err):
            for k in memory.KNOWN_KINDS:
                memory.append("manager", k, f"{k} content")
        assert err.getvalue() == ""


def test_append_empty_kind_does_not_warn():
    """Empty `kind` (some integration callers might pass it) — don't
    warn. Validate only when something IS supplied that misses the
    vocabulary."""
    import contextlib, io
    with isolated_env():
        err = io.StringIO()
        with contextlib.redirect_stderr(err):
            memory.append("manager", "", "anonymous note")
        assert err.getvalue() == ""
