"""Tests for `claudeteam say` — Feishu chat send + local mirror."""
from __future__ import annotations

import contextlib

from helpers import attr_patch, env_patch, isolated_env, run_cli
from claudeteam.feishu import chat as feishu_chat
from claudeteam.store import local_facts


def _isolated(chat_id: str = "oc_test", profile: str = ""):
    # R169: team config now carries role + emoji + color so the card
    # title renders as `{emoji} {agent} · {role}` (mirrors main's
    # `_agent_card_title`). Tests pin the new shape; older fixtures
    # had bare `{}` configs which now fall through to default emoji
    # + role="系统" — covered by a separate test.
    return isolated_env(
        team={"agents": {
            "manager": {"role": "团队主管", "emoji": "🎯", "color": "blue"},
            "worker_cc": {"role": "Claude Code 员工", "emoji": "💎",
                          "color": "purple"},
        }},
        runtime_config={"chat_id": chat_id, "lark_profile": profile},
    )


@contextlib.contextmanager
def _fake_send():
    """Replace feishu_chat.send_card with a recorder.

    R169: `claudeteam say` is card-only; the old text path is dead.
    Tests still keyed on `state['calls']` (legacy text-test ergonomics)
    but the recorder now captures send_card kwargs and synthesises
    a `text` field from the card body so existing assertions on
    `call['text']` keep working without rewrites. Send_text is still
    stubbed (no-op) in case some path accidentally falls back."""
    state = {"calls": [], "result": {"message_id": "om_fake"}}

    def fake_card(chat_id, card, *, profile="", as_user=False,
                  lark_run=None):
        # Synthesise the legacy `[<agent>] <body>` text shape from the
        # card title + body so older tests' text-string assertions
        # continue to make sense post-R169.
        title = card.get("header", {}).get("title", {}).get("content", "")
        body = ""
        try:
            body = card["body"]["elements"][0]["content"]
        except (KeyError, IndexError, TypeError):
            pass
        # Synthesised legacy shape: `[<agent>] <body>`. Title format
        # is `{emoji} {agent} · {role}` so we extract the agent slug.
        agent_slug = ""
        for tok in title.split():
            if tok and not tok.startswith(("🎯", "💎", "🟦", "🟧", "🟩",
                                            "🟪", "⚙️")) and tok != "·":
                agent_slug = tok
                break
        synth_text = f"[{agent_slug}] {body}" if agent_slug else body
        state["calls"].append({
            "chat_id": chat_id, "card": card, "text": synth_text,
            "profile": profile, "as_user": as_user, "reply_to": "",
        })
        return state["result"]

    def fake_text(*a, **kw):
        # No-op; should not be called post-R169 but keep for safety.
        return state["result"]

    with attr_patch(feishu_chat, send_card=fake_card, send_text=fake_text):
        yield state




def test_say_sends_to_chat_and_logs_locally():
    """`--no-card` keeps the old text path so this test pins the
    text-rendering format `[<agent>] <body>`."""
    with _isolated(), _fake_send() as send:
        rc, out, _ = run_cli(["say", "manager", "hello", "world", "--no-card"])
        assert rc == 0
        assert "manager → chat (message_id=om_fake)" in out
        assert send["calls"]
        call = send["calls"][0]
        assert call["chat_id"] == "oc_test"
        assert call["text"] == "[manager] hello world"
        # local mirror written
        logs = local_facts.list_logs("manager")
        assert len(logs) == 1
        assert logs[0]["type"] == "say"
        assert logs[0]["content"] == "hello world"


def test_say_default_identity_is_bot():
    with _isolated(), _fake_send() as send:
        run_cli(["say", "manager", "hi", "--no-card"])
        assert send["calls"][0]["as_user"] is False


def test_say_as_user_flag():
    with _isolated(), _fake_send() as send:
        run_cli(["say", "manager", "hi", "--no-card", "--as", "user"])
        assert send["calls"][0]["as_user"] is True


def test_say_env_var_picks_user_when_no_flag():
    with _isolated(), _fake_send() as send, \
            env_patch(CLAUDETEAM_LARK_SEND_AS="user"):
        run_cli(["say", "manager", "hi", "--no-card"])
        assert send["calls"][0]["as_user"] is True


def test_say_explicit_flag_overrides_env_var():
    with _isolated(), _fake_send() as send, \
            env_patch(CLAUDETEAM_LARK_SEND_AS="user"):
        run_cli(["say", "manager", "hi", "--no-card", "--as", "bot"])
        assert send["calls"][0]["as_user"] is False


def test_say_reply_flag_silently_dropped_post_R169():
    """R169: cards don't thread; --reply is consumed but silently
    dropped. say still succeeds (rc=0) and emits a card; only a
    one-line stderr warning surfaces the dropped threading."""
    with _isolated(), _fake_send() as send:
        rc, _, err = run_cli(["say", "manager", "hi", "--reply", "om_parent"])
        assert rc == 0
        assert len(send["calls"]) == 1
        assert "ignored" in err or "thread" in err


def test_say_no_local_skips_log_write():
    with _isolated(), _fake_send_card():
        run_cli(["say", "manager", "hi", "--no-local"])
        assert local_facts.list_logs("manager") == []


def test_say_returns_one_when_chat_id_unset():
    with _isolated(chat_id=""), _fake_send():
        rc, _, err = run_cli(["say", "manager", "hi", "--no-card"])
        assert rc == 1
        assert "chat_id not set" in err


def test_say_returns_one_when_lark_returns_none():
    with _isolated(), _fake_send() as send:
        send["result"] = None
        rc, _, err = run_cli(["say", "manager", "hi", "--no-card"])
        assert rc == 1
        assert "Feishu send failed" in err


def test_say_threads_profile():
    with _isolated(profile="prod"), _fake_send() as send:
        run_cli(["say", "manager", "hi", "--no-card"])
        assert send["calls"][0]["profile"] == "prod"


def test_say_zero_or_one_arg_returns_one():
    rc, _, err = run_cli(["say"])
    assert rc == 1
    assert "usage:" in err
    rc, _, err = run_cli(["say", "manager"])
    assert rc == 1
    assert "usage:" in err


# ── --card flag (round-99) ──────────────────────────────────────


@contextlib.contextmanager
def _fake_send_card():
    """Replace feishu_chat.send_card alongside send_text."""
    state = {"text_calls": [], "card_calls": [],
             "result": {"message_id": "om_fake_card"}}

    def fake_text(chat_id, text, **kw):
        state["text_calls"].append({"chat_id": chat_id, "text": text, **kw})
        return {"message_id": "om_fake_text"}

    def fake_card(chat_id, card, **kw):
        state["card_calls"].append({"chat_id": chat_id, "card": card, **kw})
        return state["result"]

    with attr_patch(feishu_chat, send_text=fake_text, send_card=fake_card):
        yield state


def test_say_card_flag_sends_card_not_text():
    """`--card` routes through send_card; send_text isn't touched.
    R169: title now `{emoji} {agent} · {role}` (no more bare `[agent]`)."""
    with _isolated(), _fake_send_card() as st:
        rc, _, _ = run_cli(["say", "manager", "重要决策已落地", "--card"])
    assert rc == 0
    assert len(st["card_calls"]) == 1
    assert st["text_calls"] == []
    card = st["card_calls"][0]["card"]
    # R169: title is "{emoji} {agent} · {role}" pulled from team.json
    assert card["header"]["title"]["content"] == "🎯 manager · 团队主管"
    body = card["body"]["elements"][0]["content"]
    assert "重要决策已落地" in body
    # team.json `color: blue` → blue template
    assert card["header"]["template"] == "blue"


def test_say_card_for_worker_uses_team_json_color_after_R169():
    """team.json's per-agent `color` field wins over the hard-coded
    worker_*→green default. Test fixture sets worker_cc → purple,
    matches main's worker_cc shade."""
    with _isolated(), _fake_send_card() as st:
        run_cli(["say", "worker_cc", "step 1 done", "--card"])
    card = st["card_calls"][0]["card"]
    assert card["header"]["template"] == "purple"
    assert card["header"]["title"]["content"] == "💎 worker_cc · Claude Code 员工"


def test_say_card_color_reflects_live_toml_edit():
    """REGRESSION: editing a worker's `card_color` in claudeteam.toml
    should change the very next `say` card without a router restart.
    config.agent_config goes through the lenient JSON / mtime-cached
    TOML path, so a live edit takes effect on the next call."""
    from claudeteam.runtime import paths, tunables as _tun
    with _isolated() as tmp, _fake_send_card() as st:
        # First call: default fixture has worker_cc card_color=purple
        rc, _, _ = run_cli(["say", "worker_cc", "first"])
        assert rc == 0
        first_color = st["card_calls"][0]["card"]["header"]["template"]
        assert first_color == "purple"

        # Operator edits claudeteam.toml — flip worker_cc to red
        cf = paths.config_file()
        cf.write_text(
            '[team]\nsession = "ClaudeTeam"\n\n'
            '[team.agents.manager]\ncli = "claude-code"\nrole = "主管"\n\n'
            '[team.agents.worker_cc]\ncli = "claude-code"\nrole = "Claude Code 员工"\n'
            'card_color = "red"\n',
            encoding='utf-8')
        _tun.reset_cache()

        rc, _, _ = run_cli(["say", "worker_cc", "second"])
        assert rc == 0
        second_color = st["card_calls"][1]["card"]["header"]["template"]
        assert second_color == "red", \
            f"card_color edit didn't take effect: still {second_color}"


def test_say_with_reply_warns_and_sends_card_anyway():
    """Cards don't thread; `--reply` prints a stderr warning but
    still sends the card. R169: the warn message is generic
    "--reply ignored (Feishu cards don't thread)" since there's
    no longer a --card vs --no-card distinction."""
    with _isolated(), _fake_send_card() as st:
        rc, _, err = run_cli(["say", "manager", "msg",
                              "--reply", "om_xx"])
    assert rc == 0
    assert "ignored" in err and "thread" in err
    assert len(st["card_calls"]) == 1


def test_say_default_now_sends_card_after_R168():
    """R168: default flipped — every `claudeteam say` now sends a v2
    card (colored header per role), not plain text. Boss-flagged
    convention for the test_a deploy: agent messages must look like
    structured updates in chat, not raw text. Plain text path opts
    in via the new `--no-card` flag (test below).

    R169: title format updated to `{emoji} {agent} · {role}`."""
    with _isolated(), _fake_send_card() as st:
        rc, _, _ = run_cli(["say", "manager", "plain text msg"])
    assert rc == 0
    assert len(st["card_calls"]) == 1
    assert st["text_calls"] == []
    card = st["card_calls"][0]["card"]
    assert card["header"]["title"]["content"] == "🎯 manager · 团队主管"
    body = card["body"]["elements"][0]["content"]
    assert "plain text msg" in body


def test_say_card_falls_back_to_default_emoji_when_team_json_missing_emoji():
    """team.json may not specify `emoji` — fall back to the per-agent
    default emoji table; missing-from-table agents get the system ⚙️
    glyph rather than crashing or rendering an empty space."""
    bare = isolated_env(
        team={"agents": {"manager": {"role": "管理"},
                          "worker_unknown": {"role": "未知员工"}}},
        runtime_config={"chat_id": "oc_test", "lark_profile": ""},
    )
    with bare, _fake_send_card() as st:
        run_cli(["say", "manager", "x"])
        # manager has a default-table emoji
        assert st["card_calls"][0]["card"]["header"]["title"]["content"] == \
            "🎯 manager · 管理"
        st["card_calls"].clear()
        run_cli(["say", "worker_unknown", "x"])
        # not in default table → ⚙️ system glyph
        assert st["card_calls"][0]["card"]["header"]["title"]["content"] == \
            "⚙️ worker_unknown · 未知员工"


def test_say_no_card_flag_is_a_no_op_post_R169():
    """R169: `--no-card` removed as escape hatch — every chat message
    is a card. Flag is consumed for backwards-compat but does not
    change behaviour. Boss-flagged convention: no plain-text agent
    chat in test_a deploy."""
    with _isolated(), _fake_send_card() as st:
        rc, _, _ = run_cli(["say", "manager", "收到", "--no-card"])
    assert rc == 0
    # All sends now go through send_card path; send_text is dead
    assert len(st["card_calls"]) == 1
    assert st["text_calls"] == []
    title = st["card_calls"][0]["card"]["header"]["title"]["content"]
    assert title == "🎯 manager · 团队主管"


def test_say_audit_log_failure_does_not_block_chat_send():
    """REGRESSION: audit log write is best-effort. Disk full / permission
    denied / corrupt logs.jsonl should NOT prevent the chat send — the
    boss is waiting for the message to land in the group, audit row
    is secondary."""
    def boom(*a, **kw):
        raise OSError("[Errno 28] No space left on device")

    with _isolated(), _fake_send() as send, \
            attr_patch(local_facts, append_log=boom):
        rc, _, err = run_cli(["say", "manager", "important message", "--no-card"])
    # Chat send still succeeded despite audit failing
    assert rc == 0
    # The Feishu chat got the message
    assert len(send["calls"]) == 1
    # Stderr surfaced the audit-log warning so operator knows
    assert "audit log write failed" in err


# ── Step 3: --to + chat.publish 过滤 ───────────────────────────


def _toml_with_publish(tmp_path, **kv):
    """Drop a claudeteam.toml with [chat.publish] = kv into tmp_path."""
    from claudeteam.runtime import tunables
    lines = ["[chat.publish]"]
    for k, v in kv.items():
        if v == "always":
            lines.append(f'{k} = "always"')
        elif v is True:
            lines.append(f"{k} = true")
        elif v is False:
            lines.append(f"{k} = false")
    (tmp_path / "claudeteam.toml").write_text("\n".join(lines) + "\n", encoding="utf-8")
    tunables.reset_cache()


def test_say_default_to_is_user_when_unset():
    """No --to flag → default 'user' (老板)。chat.publish.manager_to_user
    默认 True → send_card 被调。"""
    with _isolated() as tmp, _fake_send() as send:
        rc, _, _ = run_cli(["say", "manager", "hi 老板"])
    assert rc == 0
    assert len(send["calls"]) == 1


def test_say_silenced_when_publish_false():
    """publish[manager_to_worker]=false → say --to worker_cc 不发卡，
    只 log 审计。"""
    with _isolated() as tmp, _fake_send() as send:
        _toml_with_publish(tmp, manager_to_worker=False)
        rc, out, _ = run_cli(["say", "manager", "派单消息", "--to", "worker_cc"])
        assert rc == 0
        assert len(send["calls"]) == 0
        assert "silenced" in out
        # Audit log 仍然写（必须在 isolated_env 内查，state_dir 才是 tmp）
        rows = local_facts.list_logs("manager")
        assert len(rows) == 1
        assert rows[0]["content"] == "派单消息"


def test_say_passes_through_when_publish_true():
    with _isolated() as tmp, _fake_send() as send:
        _toml_with_publish(tmp, manager_to_worker=True)
        rc, _, _ = run_cli(["say", "manager", "派单", "--to", "worker_cc"])
    assert rc == 0
    assert len(send["calls"]) == 1


def test_say_passes_through_when_publish_always():
    """`always` is a hint, treated as True at runtime."""
    with _isolated() as tmp, _fake_send() as send:
        _toml_with_publish(tmp, manager_to_user="always")
        rc, _, _ = run_cli(["say", "manager", "答老板", "--to", "user"])
    assert rc == 0
    assert len(send["calls"]) == 1


def test_say_worker_to_user_default_true():
    """worker → user (worker 完工卡) — 默认 True (preserve current behavior)."""
    with _isolated() as tmp, _fake_send() as send:
        rc, _, _ = run_cli(["say", "worker_cc", "完工 ✅", "--to", "user"])
    assert rc == 0
    assert len(send["calls"]) == 1


def test_say_publish_live_edit_takes_effect_without_restart():
    """REGRESSION: editing claudeteam.toml [chat.publish] should
    affect the very next `say` call without needing to restart any
    daemon. Boss requirement: a config file is meant to live-edit.

    Verifies the tunables mtime-cache invalidation actually works
    end-to-end through commands/say.py."""
    with _isolated() as tmp, _fake_send() as send:
        # Round 1: worker_to_user = true → say goes through
        _toml_with_publish(tmp, worker_to_user=True)
        rc, _, _ = run_cli(["say", "worker_cc", "完工 1", "--to", "user"])
        assert rc == 0
        assert len(send["calls"]) == 1

        # Operator edits toml live → flip to false
        _toml_with_publish(tmp, worker_to_user=False)
        rc, out, _ = run_cli(["say", "worker_cc", "完工 2", "--to", "user"])
        assert rc == 0
        # Next call must see the new value: silenced, no chat send
        assert len(send["calls"]) == 1, "publish=false didn't take effect"
        assert "silenced" in out

        # Flip back to true → goes through again
        _toml_with_publish(tmp, worker_to_user=True)
        rc, _, _ = run_cli(["say", "worker_cc", "完工 3", "--to", "user"])
        assert rc == 0
        assert len(send["calls"]) == 2


def test_say_worker_to_manager_silenced_when_false():
    with _isolated() as tmp, _fake_send() as send:
        _toml_with_publish(tmp, worker_to_manager=False)
        rc, out, _ = run_cli(["say", "worker_cc", "进度更新", "--to", "manager"])
    assert rc == 0
    assert len(send["calls"]) == 0
    assert "silenced" in out


def test_say_unknown_to_falls_back_to_user_role():
    """`--to foobar` → receiver_role='user' fallback (safest default)."""
    with _isolated() as tmp, _fake_send() as send:
        _toml_with_publish(tmp, manager_to_user="always")
        rc, _, _ = run_cli(["say", "manager", "msg", "--to", "foobar"])
    assert rc == 0
    assert len(send["calls"]) == 1   # user_to_user → default True


def test_say_to_arg_value_required():
    """`--to` without a value should usage-error."""
    with _isolated():
        rc, _, err = run_cli(["say", "manager", "msg", "--to"])
    assert rc == 1
    assert "usage: claudeteam say" in err


# ── Step 4a: publish_overrides 单 agent 覆盖 ───────────────────


def _isolated_with_overrides(agent: str, overrides: dict, **other_agent_cfg):
    """Build an isolated_env where the named agent has publish_overrides."""
    full_cfg = {"role": "测试", "emoji": "💎", "color": "green",
                "publish_overrides": overrides, **other_agent_cfg}
    return isolated_env(
        team={"agents": {
            "manager": {"role": "团队主管", "emoji": "🎯", "color": "blue"},
            agent: full_cfg,
        }},
        runtime_config={"chat_id": "oc_test", "lark_profile": ""},
    )


def test_say_overrides_force_silence_when_global_default_true():
    """Even if chat.publish is unset (default True), agent override
    can still silence its own channel."""
    with _isolated_with_overrides("worker_cc", {"worker_to_user": False}) as tmp, \
            _fake_send() as send:
        rc, out, _ = run_cli(["say", "worker_cc", "完工", "--to", "user"])
        assert rc == 0
        assert len(send["calls"]) == 0
        assert "silenced" in out


def test_say_overrides_force_pass_when_global_silenced():
    """Override can also force-allow a channel that's globally silenced."""
    with _isolated_with_overrides("worker_cc", {"worker_to_manager": True}) as tmp, \
            _fake_send() as send:
        _toml_with_publish(tmp, worker_to_manager=False)
        rc, _, _ = run_cli(["say", "worker_cc", "进度", "--to", "manager"])
        assert rc == 0
        assert len(send["calls"]) == 1


def test_say_overrides_take_precedence_over_global():
    """When global says false but override says true, override wins."""
    with _isolated_with_overrides("worker_cc", {"worker_to_user": True}) as tmp, \
            _fake_send() as send:
        _toml_with_publish(tmp, worker_to_user=False)
        rc, _, _ = run_cli(["say", "worker_cc", "完工", "--to", "user"])
        assert rc == 0
        assert len(send["calls"]) == 1


def test_say_overrides_always_treated_as_true():
    with _isolated_with_overrides("worker_cc", {"worker_to_user": "always"}) as tmp, \
            _fake_send() as send:
        _toml_with_publish(tmp, worker_to_user=False)
        rc, _, _ = run_cli(["say", "worker_cc", "完工", "--to", "user"])
        assert rc == 0
        assert len(send["calls"]) == 1


def test_say_overrides_other_agents_unaffected():
    """worker_cc has override forcing silence; worker_codex w/o override
    follows global rule."""
    other = {"role": "数据", "emoji": "🟦", "color": "purple"}
    with isolated_env(team={"agents": {
        "manager": {"role": "主管"},
        "worker_cc": {"role": "策划", "publish_overrides": {"worker_to_user": False}},
        "worker_codex": other,
    }}, runtime_config={"chat_id": "oc_test", "lark_profile": ""}) as tmp, \
            _fake_send() as send:
        # worker_cc → 静默
        rc1, _, _ = run_cli(["say", "worker_cc", "完工 cc", "--to", "user"])
        assert len(send["calls"]) == 0
        # worker_codex → 通过（默认 True）
        rc2, _, _ = run_cli(["say", "worker_codex", "完工 codex", "--to", "user"])
        assert len(send["calls"]) == 1
        assert rc1 == 0 and rc2 == 0


def test_say_no_override_falls_through_to_global():
    """Agent without publish_overrides → global rule applies."""
    with _isolated() as tmp, _fake_send() as send:
        _toml_with_publish(tmp, manager_to_worker=False)
        rc, out, _ = run_cli(["say", "manager", "派单", "--to", "worker_cc"])
        assert rc == 0
        assert len(send["calls"]) == 0
        assert "silenced" in out
