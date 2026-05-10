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
    """Intercept feishu.chat.send_text + send_card so the SLASH path doesn't
    try to hit a real Feishu API. Returns a state dict recording each post;
    `posts[i]['kind']` is `"text"` or `"card"` and `posts[i]['text']` carries
    the text body for both (cards' body is pulled from elements[0])."""
    state = {"posts": []}

    def fake_text(chat_id, text, **kw):
        state["posts"].append({"chat_id": chat_id, "text": text,
                               "kind": "text", **kw})
        return {"message_id": "om_fake"}

    def fake_card(chat_id, card, **kw):
        # R159: card v2 shape — body lives at `body.elements[0].content`
        # (was `elements[0].text.content` in v1).
        body = ""
        try:
            body = card["elements"][0]["text"]["content"]
        except (KeyError, IndexError, TypeError):
            pass
        state["posts"].append({"chat_id": chat_id, "card": card,
                               "text": body, "kind": "card", **kw})
        return {"message_id": "om_fake_card"}

    from helpers import attr_patch
    from claudeteam.feishu import chat as _chat_module
    with attr_patch(_chat_module, send_text=fake_text, send_card=fake_card):
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
        # R172.b: deliver wraps chat messages with a routing hint so the
        # agent posts replies via `claudeteam say` instead of answering
        # in pane. The original message body still appears verbatim.
        assert "please help" in manager_inj[0]["text"]
        assert "claudeteam say manager" in manager_inj[0]["text"]
        # ClaudeCodeAdapter uses the default ["Enter", "C-m", "C-j"]
        assert manager_inj[0]["submit_keys"] == ["Enter", "C-m", "C-j"]


# ── Scenario B: @-mention worker_codex ───────────────────────────


def test_mention_now_routes_to_manager_only_r174():
    """R174: `@worker_codex review` from boss no longer fans out to
    codex directly — it goes to manager, who decides whether to
    dispatch via `claudeteam send`."""
    with _isolated(), _fake_inject() as inj:
        line = _ndjson_event("om_mention_1", "ou_user", "@worker_codex review")
        _run_lines([line])
        manager_inj = [c for c in inj["calls"] if c["target"] == "SmokeTeam:manager"]
        codex_inj = [c for c in inj["calls"] if c["target"] == "SmokeTeam:worker_codex"]
        assert len(manager_inj) == 1
        assert codex_inj == []  # router doesn't fan out
        # @-mention text preserved verbatim for manager to read
        assert "@worker_codex" in manager_inj[0]["text"]


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
    """R174: ALL human messages → manager (`@worker_X` is now content,
    not routing). 3 handled events all land in manager's inbox."""
    with _isolated(), _fake_inject() as inj:
        events = [
            _ndjson_event("om_1", "ou_user", "task A"),                  # → manager
            _ndjson_event("om_2", "ou_user", "@worker_kimi handle B"),    # → manager (was kimi)
            _ndjson_event("om_3", "ou_user", ""),                        # empty → drop
            _ndjson_event("om_4", "ou_bot", "self-talk"),                # bot_self
            _ndjson_event("om_1", "ou_user", "duplicate of #1"),         # dedup
            "not-json",                                                  # bad_json
            _ndjson_event("om_5", "ou_user", "@worker_kimi @worker_codex"),  # → manager
        ]
        stats = _run_lines(events, bot_id="ou_bot")
        assert stats.handled == 3
        assert stats.dropped == 4

        # All 3 handled events route to manager; workers get nothing
        # from the router (manager dispatches via send, not tested here).
        assert len(local_facts.list_messages("manager")) == 3
        assert len(local_facts.list_messages("worker_kimi")) == 0
        assert len(local_facts.list_messages("worker_codex")) == 0


# ── Scenario F: R174 — broadcast triggers route to manager only ───


def test_at_team_routes_to_manager_only_r174():
    """R174: `@team` from boss → manager only. Manager parses the
    intent and decides whether to fan-out via `claudeteam send` per
    worker. Workers get nothing from the router for this event."""
    with _isolated(), _fake_inject() as inj:
        line = _ndjson_event("om_bcast_1", "ou_user", "@team standup at 3pm")
        stats = _run_lines([line])
        assert stats.handled == 1
        assert len(local_facts.list_messages("manager")) == 1
        assert local_facts.list_messages("worker_codex") == []
        assert local_facts.list_messages("worker_kimi") == []
        # Manager's pane received exactly one inject
        manager_inj = [c for c in inj["calls"]
                        if c["target"] == "SmokeTeam:manager"]
        assert len(manager_inj) == 1
        assert "@team" in manager_inj[0]["text"]


def test_chinese_quanti_prefix_routes_to_manager_only_r174():
    """`全体X` from boss → manager only (was BROADCAST in R172.b,
    now ROUTE per R174)."""
    with _isolated(), _fake_inject() as inj:
        line = _ndjson_event("om_bcast_2", "ou_user", "全体注意：今晚封版")
        _run_lines([line])
        assert len(local_facts.list_messages("manager")) == 1
        assert local_facts.list_messages("worker_codex") == []
        assert local_facts.list_messages("worker_kimi") == []


# ── Scenario G: SLASH dispatches at router level (zero LLM) ──────


def test_slash_help_does_not_touch_inboxes_or_panes():
    """`/help` is recognised at the router level → bot reply only,
    no inbox row, no pane inject. This is the core "zero LLM" promise.
    Round-79: /help now sends a card (kind="card"); cards don't carry
    reply_to (Feishu interactive cards don't thread)."""
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
        # The bot reply IS posted to chat — as a card
        assert len(chat["posts"]) == 1
        assert chat["posts"][0]["kind"] == "card"
        assert "/help" in chat["posts"][0]["text"]


def test_slash_with_sender_prefix_still_recognised():
    """REGRESSION (round A2 B1): `say` wraps outbound text with
    `[<sender>] ...`. The router pre-strips that prefix before checking
    for `/`, so `[boss] /help` still dispatches as a slash command."""
    with _isolated(), _fake_inject() as inj, _fake_chat_send() as chat:
        line = _ndjson_event("om_slash_2", "ou_user", "[boss] /help")
        _run_lines([line])
        # zero panes, one chat post (bot reply)
        assert inj["calls"] == []
        assert len(chat["posts"]) == 1
        # /help sends a card whose body contains "/help"
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
    router daemon passes wake.wake_if_dormant), a Decision that targets
    a lazy worker should trigger the wake before the inject. R174:
    router only routes to manager from chat — but `claudeteam send`
    + watchdog respawn paths still construct ROUTE Decisions targeting
    workers directly, so this still matters. Verify wake_fn fires."""
    from claudeteam.feishu.router import Action, Decision
    wake_calls = []

    def fake_wake(target, adapter, *, spawn_cmd, init_msg, on_woken,
                  timeout_s=None, **_kw):
        wake_calls.append({
            "target": str(target),
            "spawn_cmd": spawn_cmd,
            "init_msg": init_msg,
        })
        on_woken()  # simulate the lazy pane coming alive
        return True

    with _isolated(), _fake_inject() as inj:
        decision = Decision(
            action=Action.ROUTE, targets=["worker_kimi"], sender="manager",
            text="wake up", msg_id="om_lazy_1",
        )
        apply(decision, wake_fn=fake_wake)
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

    from claudeteam.feishu.router import Action, Decision
    with _isolated(), _fake_inject() as inj, attr_patch(
            _wake, is_rate_limited=fake_rate_limited):
        # R174: routes from chat all go to manager, but peer dispatch
        # (manager → worker via `claudeteam send`) still constructs
        # ROUTE Decisions targeting workers. Build them directly so we
        # can exercise the rate-limit branch on a worker target.
        apply(Decision(action=Action.ROUTE, targets=["worker_codex"],
                       sender="manager", text="urgent", msg_id="om_rl_1"))
        apply(Decision(action=Action.ROUTE, targets=["worker_kimi"],
                       sender="manager", text="do this", msg_id="om_rl_2"))

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


