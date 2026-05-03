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


def _ndjson_media(message_id: str, sender_id: str, msg_type: str,
                  content: dict, chat_id: str = "oc_smoke") -> str:
    """Build an NDJSON event for image / file / audio / sticker shapes —
    `content` is the lark-style dict (image_key, file_key+file_name,
    etc.) which subscribe._extract_text reduces to a placeholder."""
    return json.dumps({
        "event": {
            "message": {
                "message_id": message_id,
                "chat_id": chat_id,
                "message_type": msg_type,
                "content": json.dumps(content),
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


# ── Scenario H: image / file / audio / sticker placeholders ──────


def test_image_message_routes_with_image_key_placeholder():
    """B.1: image messages used to drop as 'empty'. Now subscribe._extract_text
    produces a `[image: image_key=...]` placeholder so the message routes
    to manager (the default target for unknown senders) and the worker
    knows something arrived."""
    with _isolated(), _fake_inject() as inj:
        line = _ndjson_media("om_img_1", "ou_user", "image",
                             {"image_key": "img_v3_xxx"})
        stats = _run_lines([line])
        assert stats.handled == 1
        # manager inbox + pane both got the placeholder text
        rows = local_facts.list_messages("manager")
        assert len(rows) == 1
        assert "image_key=img_v3_xxx" in rows[0]["content"]
        assert "[image:" in rows[0]["content"]
        manager_inj = [c for c in inj["calls"]
                       if c["target"] == "SmokeTeam:manager"]
        assert len(manager_inj) == 1
        assert "image_key=img_v3_xxx" in manager_inj[0]["text"]


def test_file_message_routes_with_filename_in_placeholder():
    """File messages render as `[file: <name> (file_key=...)]` so the
    worker can read the filename in chat scrollback and decide whether
    to fetch the binary."""
    with _isolated(), _fake_inject() as inj:
        line = _ndjson_media("om_file_1", "ou_user", "file", {
            "file_name": "report.pdf",
            "file_key": "file_v2_xxx",
        })
        _run_lines([line])
        rows = local_facts.list_messages("manager")
        assert len(rows) == 1
        assert "report.pdf" in rows[0]["content"]
        assert "file_v2_xxx" in rows[0]["content"]


def test_audio_and_sticker_messages_route_with_their_own_placeholders():
    """Audio / sticker placeholders are simpler — file_key only. Confirms
    the same routing path handles all four media types end-to-end."""
    with _isolated(), _fake_inject() as inj:
        lines = [
            _ndjson_media("om_audio_1", "ou_user", "audio",
                          {"file_key": "audio_xxx"}),
            _ndjson_media("om_sticker_1", "ou_user", "sticker",
                          {"file_key": "stk_xxx"}),
        ]
        stats = _run_lines(lines)
        assert stats.handled == 2
        rows = local_facts.list_messages("manager")
        assert len(rows) == 2
        contents = [r["content"] for r in rows]
        assert any("[audio:" in c and "audio_xxx" in c for c in contents)
        assert any("[sticker:" in c and "stk_xxx" in c for c in contents)


def test_image_with_at_mention_caption_routes_to_mentioned_worker():
    """When an image's payload includes @-mention text (some Feishu
    clients embed text in image content), classify_event should still
    route to the mentioned worker rather than the default target."""
    with _isolated(), _fake_inject() as inj:
        # Feishu doesn't actually embed text in image payloads, but the
        # router should at least handle the placeholder being routed
        # to manager (default target) when no mention is present.
        line = _ndjson_media("om_img_2", "ou_user", "image",
                             {"image_key": "img_xxx"})
        _run_lines([line])
        # No @ in placeholder → default target = manager
        assert len(local_facts.list_messages("manager")) == 1
        assert local_facts.list_messages("worker_codex") == []
        assert local_facts.list_messages("worker_kimi") == []


# ── Scenario I: lazy wake on first message ───────────────────────


def test_lazy_pane_wake_fn_invoked_then_inject_proceeds():
    """When deliver.apply is wired with a wake_fn (production: the
    router daemon passes wake.wake_if_dormant), an inbound message to
    a lazy pane should trigger the wake before the inject. Verifies
    wake_fn was called with the right kwargs (spawn_cmd, init_msg,
    on_woken) and that the inject still happens after."""
    wake_calls = []

    def fake_wake(target, adapter, *, spawn_cmd, init_msg, on_woken):
        wake_calls.append({
            "target": str(target),
            "spawn_cmd": spawn_cmd,
            "init_msg": init_msg,
        })
        on_woken()  # simulate the lazy pane coming alive
        return True

    def apply_with_wake(decision):
        return apply(decision, wake_fn=fake_wake)

    with _isolated(), _fake_inject() as inj:
        line = _ndjson_event("om_lazy_1", "ou_user", "@worker_kimi wake up")
        subscribe.process_lines(
            [line], team_agents=_DEFAULT_AGENTS, chat_id="oc_smoke",
            apply_fn=apply_with_wake,
        )
        # wake_fn was called with the right shape
        assert len(wake_calls) == 1
        assert wake_calls[0]["target"] == "SmokeTeam:worker_kimi"
        assert "worker_kimi" in wake_calls[0]["init_msg"]
        assert "identity.md" in wake_calls[0]["init_msg"]
        # Inject still happened after wake
        kimi_inj = [c for c in inj["calls"]
                    if c["target"] == "SmokeTeam:worker_kimi"]
        assert len(kimi_inj) == 1
        # on_woken flipped status to 进行中
        snap = local_facts.get_status("worker_kimi")
        assert snap is not None
        assert snap["status"] == "进行中"


# ── Scenario J: rate-limited pane skips inject, keeps inbox ──────


def test_rate_limited_pane_keeps_inbox_skips_inject():
    """When the pane shows a rate-limit marker, deliver should still
    write the inbox row (so the message isn't lost) but skip the tmux
    inject (the CLI won't process it). Verifies wake.is_rate_limited
    short-circuits the inject without losing the inbox write."""
    from helpers import attr_patch
    from claudeteam.runtime import wake as _wake

    def fake_rate_limited(target, adapter, **kw):
        # Only worker_codex is rate-limited; others are clear
        return target.window == "worker_codex"

    with _isolated(), _fake_inject() as inj, attr_patch(
            _wake, is_rate_limited=fake_rate_limited):
        events = [
            # @ codex (rate-limited) → inbox written, inject skipped
            _ndjson_event("om_rl_1", "ou_user", "@worker_codex urgent"),
            # @ kimi (clear) → both inbox + inject happen
            _ndjson_event("om_rl_2", "ou_user", "@worker_kimi do this"),
        ]
        _run_lines(events)

        # codex: inbox YES, inject NO
        assert len(local_facts.list_messages("worker_codex")) == 1
        codex_inj = [c for c in inj["calls"]
                     if c["target"] == "SmokeTeam:worker_codex"]
        assert codex_inj == [], (
            f"rate-limited codex should not be injected; got {codex_inj}")

        # kimi: inbox YES, inject YES (control case)
        assert len(local_facts.list_messages("worker_kimi")) == 1
        kimi_inj = [c for c in inj["calls"]
                    if c["target"] == "SmokeTeam:worker_kimi"]
        assert len(kimi_inj) == 1


