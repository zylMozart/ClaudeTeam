"""Tests for store/acp_queue.py — the ACP delivery state machine."""
from __future__ import annotations

from helpers import isolated_env
from claudeteam.store import acp_queue


def test_enqueue_then_claim_marks_prompting():
    with isolated_env():
        qid = acp_queue.enqueue("w", "hello", sender="user", local_id="msg_1")
        row = acp_queue.claim_next("w")
        assert row is not None
        assert row["qid"] == qid
        assert row["state"] == acp_queue.PROMPTING
        assert row["attempts"] == 1
        assert row["local_id"] == "msg_1"


def test_claim_is_fifo_and_skips_claimed():
    with isolated_env():
        q1 = acp_queue.enqueue("w", "first")
        q2 = acp_queue.enqueue("w", "second")
        assert acp_queue.claim_next("w")["qid"] == q1
        assert acp_queue.claim_next("w")["qid"] == q2
        assert acp_queue.claim_next("w") is None


def test_settle_done_records_stop_reason():
    with isolated_env():
        qid = acp_queue.enqueue("w", "hi")
        acp_queue.claim_next("w")
        assert acp_queue.settle("w", qid, acp_queue.DONE, stop_reason="end_turn")
        row = acp_queue.rows("w")[0]
        assert row["state"] == acp_queue.DONE
        assert row["stop_reason"] == "end_turn"


def test_recover_stuck_rearms_prompting_rows():
    """Router crash mid-turn: the prompting row must come back as pending
    (at-least-once), not vanish."""
    with isolated_env():
        qid = acp_queue.enqueue("w", "in flight when host died")
        acp_queue.claim_next("w")
        rearmed = acp_queue.recover_stuck("w")
        assert [r["qid"] for r in rearmed] == [qid]
        assert acp_queue.rows("w", state=acp_queue.PENDING)[0]["qid"] == qid
        # claim again → attempts increments
        assert acp_queue.claim_next("w")["attempts"] == 2


def test_recover_stuck_parks_poison_row_after_max_attempts():
    with isolated_env():
        qid = acp_queue.enqueue("w", "poison")
        for _ in range(acp_queue.MAX_ATTEMPTS):
            assert acp_queue.claim_next("w") is not None
            acp_queue.recover_stuck("w")
        row = acp_queue.rows("w")[0]
        assert row["qid"] == qid
        assert row["state"] == acp_queue.FAILED
        assert "gave up" in row["error"]


def test_control_rows_consumed_immediately_and_bypass_prompts():
    with isolated_env():
        acp_queue.enqueue("w", "long prompt backlog")
        acp_queue.enqueue("w", "", kind="cancel")
        ctl = acp_queue.take_control_rows("w")
        assert [r["kind"] for r in ctl] == ["cancel"]
        # prompt row untouched, cancel row settled
        assert len(acp_queue.rows("w", state=acp_queue.PENDING)) == 1
        assert acp_queue.rows("w", state=acp_queue.DONE)[0]["kind"] == "cancel"


def test_has_inflight_tracks_claimed_prompt():
    with isolated_env():
        acp_queue.enqueue("w", "x")
        assert not acp_queue.has_inflight("w")
        acp_queue.claim_next("w")
        assert acp_queue.has_inflight("w")


def test_settled_rows_are_bounded():
    with isolated_env():
        for i in range(acp_queue.KEEP_SETTLED + 50):
            qid = acp_queue.enqueue("w", f"m{i}")
            acp_queue.claim_next("w")
            acp_queue.settle("w", qid, acp_queue.DONE, stop_reason="end_turn")
        settled = acp_queue.rows("w", state=acp_queue.DONE)
        assert len(settled) == acp_queue.KEEP_SETTLED
        # newest survive the trim
        assert settled[-1]["text"] == f"m{acp_queue.KEEP_SETTLED + 49}"


def test_queues_are_per_agent():
    with isolated_env():
        acp_queue.enqueue("a", "for a")
        acp_queue.enqueue("b", "for b")
        assert acp_queue.claim_next("a")["text"] == "for a"
        assert acp_queue.claim_next("b")["text"] == "for b"
