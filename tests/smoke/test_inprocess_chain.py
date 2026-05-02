"""End-to-end in-process smoke for the rebuild — no network, no real tmux.

Wires `feishu.subscribe.process_lines` (real) → `feishu.deliver.apply`
(real) → `local_facts` (real, isolated tempdir) and a fake tmux.inject so
the assertion is "inbox got the row + tmux pane received the right keys".

This is as close to live smoke as the gate can run.  Real live smoke
(actual lark-cli + tmux + Feishu) lives in tests/smoke/scenarios/*.md
and runs under operator supervision.
"""
from __future__ import annotations

import contextlib
import json

from helpers import isolated_env, tmux_patch
from claudeteam.feishu import subscribe
from claudeteam.feishu.deliver import apply
from claudeteam.store import local_facts


_TEAM = {
    "session": "SmokeTeam",
    "agents": {
        "manager":      {"cli": "claude-code"},
        "worker_codex": {"cli": "codex-cli"},
        "worker_kimi":  {"cli": "kimi-code"},
    },
}


def _isolated():
    return isolated_env(team=_TEAM,
                        runtime_config={"chat_id": "oc_smoke", "lark_profile": ""})


@contextlib.contextmanager
def _fake_inject():
    """Replace tmux.inject with a recorder that always returns True."""
    state = {"calls": []}

    def fake(target, text, *, submit_keys=None):
        state["calls"].append({
            "target": str(target),
            "text": text,
            "submit_keys": submit_keys,
        })
        return True

    with tmux_patch(inject=fake):
        yield state


def _ndjson_event(message_id: str, sender_id: str, text: str,
                  chat_id: str = "oc_smoke") -> str:
    return json.dumps({
        "event": {
            "message": {
                "message_id": message_id,
                "chat_id": chat_id,
                "message_type": "text",
                "content": json.dumps({"text": text}),
            },
            "sender": {"sender_id": {"open_id": sender_id}},
        }
    })


# ── Scenario A: human → manager ──────────────────────────────────


def test_human_message_lands_in_manager_inbox_and_pane():
    with _isolated(), _fake_inject() as inj:
        line = _ndjson_event("om_human_1", "ou_user", "please help")
        stats = subscribe.process_lines(
            [line],
            team_agents=["manager", "worker_codex", "worker_kimi"],
            chat_id="oc_smoke",
            apply_fn=apply,
        )
        assert stats.handled == 1
        assert stats.dropped == 0

        # inbox got the row
        rows = local_facts.list_messages("manager")
        assert len(rows) == 1
        assert rows[0]["content"] == "please help"
        assert rows[0]["from"] == "user"

        # manager pane received an inject
        manager_inj = [c for c in inj["calls"] if c["target"] == "SmokeTeam:manager"]
        assert len(manager_inj) == 1
        assert manager_inj[0]["text"] == "please help"
        # ClaudeCodeAdapter uses the default ["Enter", "C-m", "C-j"]
        assert manager_inj[0]["submit_keys"] == ["Enter", "C-m", "C-j"]


# ── Scenario B: @-mention worker_codex ───────────────────────────


def test_mention_routes_to_codex_with_m_enter_first():
    with _isolated(), _fake_inject() as inj:
        line = _ndjson_event("om_mention_1", "ou_user", "@worker_codex review")
        subscribe.process_lines(
            [line],
            team_agents=["manager", "worker_codex", "worker_kimi"],
            chat_id="oc_smoke",
            apply_fn=apply,
        )
        # codex got it, manager did not
        codex_inj = [c for c in inj["calls"] if c["target"] == "SmokeTeam:worker_codex"]
        assert len(codex_inj) == 1
        manager_inj = [c for c in inj["calls"] if c["target"] == "SmokeTeam:manager"]
        assert manager_inj == []

        # Codex submits with M-Enter first per its adapter
        assert codex_inj[0]["submit_keys"][0] == "M-Enter"


# ── Scenario C: dedup ────────────────────────────────────────────


def test_repeated_message_id_only_delivered_once():
    with _isolated(), _fake_inject() as inj:
        same_line = _ndjson_event("om_dup", "ou_user", "ping")
        stats = subscribe.process_lines(
            [same_line, same_line, same_line],
            team_agents=["manager"],
            chat_id="oc_smoke",
            apply_fn=apply,
        )
        assert stats.handled == 1
        assert stats.dropped == 2
        assert stats.drops_by_reason.get("dedup") == 2

        # only one inbox row
        rows = local_facts.list_messages("manager")
        assert len(rows) == 1


# ── Scenario D: cross-team isolation ─────────────────────────────


def test_message_from_other_chat_is_ignored():
    with _isolated(), _fake_inject() as inj:
        wrong = _ndjson_event("om_other_1", "ou_user", "hi", chat_id="oc_other_team")
        stats = subscribe.process_lines(
            [wrong],
            team_agents=["manager"],
            chat_id="oc_smoke",
            apply_fn=apply,
        )
        assert stats.handled == 0
        assert stats.drops_by_reason.get("cross_team") == 1
        assert local_facts.list_messages("manager") == []
        assert inj["calls"] == []


# ── Scenario E: full mixed traffic ───────────────────────────────


def test_mixed_traffic_classifies_each_event_correctly():
    with _isolated(), _fake_inject() as inj:
        events = [
            _ndjson_event("om_1", "ou_user", "task A"),                  # → manager
            _ndjson_event("om_2", "ou_user", "@worker_kimi handle B"),    # → worker_kimi
            _ndjson_event("om_3", "ou_user", ""),                        # empty → drop
            _ndjson_event("om_4", "ou_bot", "self-talk"),                # bot_self
            _ndjson_event("om_1", "ou_user", "duplicate of #1"),         # dedup
            "not-json",                                                  # bad_json
            _ndjson_event("om_5", "ou_user", "@worker_kimi @worker_codex"),  # → both
        ]
        stats = subscribe.process_lines(
            events,
            team_agents=["manager", "worker_codex", "worker_kimi"],
            chat_id="oc_smoke",
            bot_id="ou_bot",
            apply_fn=apply,
        )
        assert stats.handled == 3  # om_1, om_2, om_5
        assert stats.dropped == 4  # empty, bot_self, dedup, bad_json

        # inbox: manager (om_1) + kimi (om_2 + om_5) + codex (om_5)
        assert len(local_facts.list_messages("manager")) == 1
        assert len(local_facts.list_messages("worker_kimi")) == 2
        assert len(local_facts.list_messages("worker_codex")) == 1


