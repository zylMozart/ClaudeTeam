"""End-to-end in-process integration test for the rebuild.

Wires `feishu.subscribe.process_lines` (real) → `feishu.deliver.apply`
(real) → `local_facts` (real, isolated tempdir) and a fake tmux.inject so
the assertion is "inbox got the row + tmux pane received the right keys".

True host-live smoke (real lark-cli + tmux + Feishu) is the README-driven
bootstrap test, plus the operator playbooks in tests/scenarios/*.md.
"""
from __future__ import annotations

import contextlib
import json

from helpers import isolated_env, tmux_patch
from claudeteam.feishu import subscribe
from claudeteam.feishu.deliver import apply
from claudeteam.store import local_facts


@contextlib.contextmanager
def _fake_chat_send():
    """Intercept feishu.chat.send_text so the SLASH path doesn't try
    to hit a real Feishu API. Returns a state dict recording each post.
    """
    state = {"posts": []}

    def fake(chat_id, text, **kw):
        state["posts"].append({"chat_id": chat_id, "text": text, **kw})
        return {"message_id": "om_fake"}

    from helpers import attr_patch
    from claudeteam.feishu import chat as _chat_module
    with attr_patch(_chat_module, send_text=fake):
        yield state


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


_DEFAULT_AGENTS = ["manager", "worker_codex", "worker_kimi"]


def _run_lines(lines, *, team_agents=None, **extra):
    """Drive subscribe.process_lines with the smoke deployment defaults
    (chat_id=oc_smoke, real apply, the 3-agent team). Override anything
    via kwargs."""
    return subscribe.process_lines(
        lines,
        team_agents=team_agents or _DEFAULT_AGENTS,
        chat_id="oc_smoke",
        apply_fn=apply,
        **extra,
    )


# ── Scenario A: human → manager ──────────────────────────────────


def test_human_message_lands_in_manager_inbox_and_pane():
    with _isolated(), _fake_inject() as inj:
        line = _ndjson_event("om_human_1", "ou_user", "please help")
        stats = _run_lines([line])
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
        _run_lines([line])
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
        stats = _run_lines([same_line, same_line, same_line], team_agents=["manager"])
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
        stats = _run_lines([wrong], team_agents=["manager"])
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
        stats = _run_lines(events, bot_id="ou_bot")
        assert stats.handled == 3  # om_1, om_2, om_5
        assert stats.dropped == 4  # empty, bot_self, dedup, bad_json

        # inbox: manager (om_1) + kimi (om_2 + om_5) + codex (om_5)
        assert len(local_facts.list_messages("manager")) == 1
        assert len(local_facts.list_messages("worker_kimi")) == 2
        assert len(local_facts.list_messages("worker_codex")) == 1


# ── Scenario F: BROADCAST fans out to non-sender agents ──────────


def test_broadcast_token_at_team_fans_out_to_all_workers():
    """`@team` from a human reaches every team agent's inbox + pane.
    Sender is unknown (human), so all 3 team_agents get the message."""
    with _isolated(), _fake_inject() as inj:
        line = _ndjson_event("om_bcast_1", "ou_user", "@team standup at 3pm")
        stats = _run_lines([line])
        assert stats.handled == 1

        for agent in _DEFAULT_AGENTS:
            assert len(local_facts.list_messages(agent)) == 1, (
                f"{agent} should have 1 inbox row from broadcast")
            agent_inj = [c for c in inj["calls"]
                         if c["target"] == f"SmokeTeam:{agent}"]
            assert len(agent_inj) == 1, (
                f"{agent} pane should have 1 inject from broadcast")


def test_broadcast_chinese_quanti_prefix_routes_same_way():
    """`全体X` Chinese broadcast trigger — same fanout as @team."""
    with _isolated(), _fake_inject() as inj:
        line = _ndjson_event("om_bcast_2", "ou_user", "全体注意：今晚封版")
        _run_lines([line])
        assert len(local_facts.list_messages("manager")) == 1
        assert len(local_facts.list_messages("worker_codex")) == 1
        assert len(local_facts.list_messages("worker_kimi")) == 1


def test_broadcast_from_known_agent_excludes_sender():
    """If [worker_codex] @team broadcasts, codex's own inbox shouldn't
    receive a copy — broadcast targets non-sender agents."""
    with _isolated(), _fake_inject() as inj:
        line = _ndjson_event("om_bcast_3", "ou_user",
                             "[worker_codex] @team status sync")
        _run_lines([line])
        # manager + kimi got it; codex did not
        assert len(local_facts.list_messages("manager")) == 1
        assert len(local_facts.list_messages("worker_kimi")) == 1
        assert local_facts.list_messages("worker_codex") == []


# ── Scenario G: SLASH dispatches at router level (zero LLM) ──────


def test_slash_help_does_not_touch_inboxes_or_panes():
    """`/help` is recognised at the router level → bot reply only,
    no inbox row, no pane inject. This is the core "zero LLM" promise."""
    with _isolated(), _fake_inject() as inj, _fake_chat_send() as chat:
        line = _ndjson_event("om_help_1", "ou_user", "/help")
        stats = _run_lines([line])
        assert stats.handled == 1

        # Zero panes touched
        assert inj["calls"] == [], (
            f"/help should not inject into any pane; got {inj['calls']}")
        # Zero inbox rows written
        for agent in _DEFAULT_AGENTS:
            assert local_facts.list_messages(agent) == []
        # The bot reply IS posted to chat
        assert len(chat["posts"]) == 1
        assert "/help" in chat["posts"][0]["text"]
        # ...with reply_to threading back to the boss's message
        assert chat["posts"][0]["reply_to"] == "om_help_1"


def test_slash_with_sender_prefix_still_recognised():
    """REGRESSION (round A2 B1): `say` wraps outbound text with
    `[<sender>] ...`. The router pre-strips that prefix before checking
    for `/`, so `[boss] /team` still dispatches as a slash command."""
    with _isolated(), _fake_inject() as inj, _fake_chat_send() as chat:
        line = _ndjson_event("om_slash_2", "ou_user", "[boss] /help")
        _run_lines([line])
        # zero panes, one chat post (bot reply)
        assert inj["calls"] == []
        assert len(chat["posts"]) == 1
        # The reply is the help text — "/help" appears in the body
        assert "/help" in chat["posts"][0]["text"]


def test_unknown_slash_still_zero_llm_returns_help_hint():
    """Unrecognised slash commands like `/madeupthing` still get handled
    at the router — bot replies with a "use /help" hint, no pane touched."""
    with _isolated(), _fake_inject() as inj, _fake_chat_send() as chat:
        line = _ndjson_event("om_slash_3", "ou_user", "/madeupthing")
        _run_lines([line])
        assert inj["calls"] == []
        assert len(chat["posts"]) == 1
        assert "未知斜杠命令" in chat["posts"][0]["text"]
        assert "/help" in chat["posts"][0]["text"]


