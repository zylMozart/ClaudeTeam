#!/usr/bin/env python3
"""No-live tests for router daemon runtime behavior."""
from __future__ import annotations

import os
import sys
import tempfile
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

from claudeteam.messaging.router import daemon
from claudeteam.messaging.router.daemon import _RouterRuntime
from claudeteam.messaging.router.state import RouterState


class Adapter:
    def __init__(self, name="claude"):
        self.name = name

    def process_name(self):
        return self.name

    def ready_markers(self):
        return ["ready"]

    def submit_keys(self):
        return ["Enter"]


class Patch:
    def __init__(self, obj, **items):
        self.obj = obj
        self.items = items
        self.old = {}

    def __enter__(self):
        for key, value in self.items.items():
            self.old[key] = getattr(self.obj, key)
            setattr(self.obj, key, value)

    def __exit__(self, exc_type, exc, tb):
        for key, value in self.old.items():
            setattr(self.obj, key, value)


def make_runtime(tmp: str) -> _RouterRuntime:
    team_file = os.path.join(tmp, "team.json")
    Path(team_file).write_text('{"agents":{"manager":{},"devops":{}}}\n', encoding="utf-8")
    old_state = os.environ.get("CLAUDETEAM_STATE_DIR")
    os.environ["CLAUDETEAM_STATE_DIR"] = os.path.join(tmp, "state")
    try:
        rt = _RouterRuntime(
            cfg={"chat_id": "chat", "_lark_cli": ["lark"], "_tmux_session": "sess", "_images_dir": tmp},
            team_file=team_file,
            scripts_dir=os.path.join(tmp, "scripts"),
        )
    finally:
        if old_state is None:
            os.environ.pop("CLAUDETEAM_STATE_DIR", None)
        else:
            os.environ["CLAUDETEAM_STATE_DIR"] = old_state
    rt._adapter = lambda name: Adapter()
    rt._render_inbox = lambda text: text
    rt._render_tmux = lambda title, subtitle, content, agent: f"{title}:{agent}:{content}"
    return rt


def test_deliver_manager_user_message_uses_realtime_options():
    with tempfile.TemporaryDirectory() as tmp:
        rt = make_runtime(tmp)
        calls = []
        with Patch(daemon, wake_on_deliver=lambda *a, **k: True):
            with Patch(daemon, agent_has_live_cli=lambda *a, **k: True):
                import claudeteam.runtime.tmux_utils as tmux_utils
                with Patch(tmux_utils, inject_when_idle=lambda *a, **k: calls.append((a, k)) or True):
                    rt._deliver("manager", "hello", None, "m1")
        assert calls
        args, kwargs = calls[0]
        assert args[:2] == ("sess", "manager")
        assert kwargs["wait_secs"] == 3
        assert kwargs["force_after_wait"] is True
        assert kwargs["submit_keys"] == ["Enter"]


def test_deliver_non_manager_uses_queued_safe_options():
    with tempfile.TemporaryDirectory() as tmp:
        rt = make_runtime(tmp)
        calls = []
        with Patch(daemon, wake_on_deliver=lambda *a, **k: True):
            import claudeteam.runtime.tmux_utils as tmux_utils
            with Patch(tmux_utils, inject_when_idle=lambda *a, **k: calls.append((a, k)) or True):
                rt._deliver("devops", "hello", None, "m1")
        assert calls
        _, kwargs = calls[0]
        assert kwargs["wait_secs"] == 30
        assert kwargs["force_after_wait"] is False
        assert kwargs["submit_keys"] == ["Enter"]


def test_deliver_pending_skips_direct_inject_and_enqueues():
    with tempfile.TemporaryDirectory() as tmp:
        rt = make_runtime(tmp)
        rt._has_pending = lambda agent: True
        enqueued = []
        rt._enqueue = lambda *a, **k: enqueued.append((a, k))
        with Patch(daemon, wake_on_deliver=lambda *a, **k: True):
            import claudeteam.runtime.tmux_utils as tmux_utils
            with Patch(tmux_utils, inject_when_idle=lambda *a, **k: (_ for _ in ()).throw(AssertionError("should not inject"))):
                rt._deliver("devops", "hello", None, "m1")
        assert enqueued
        assert enqueued[0][0][0] == "devops"


def test_handle_image_empty_text_advances_cursor_and_enqueues_download_notice():
    with tempfile.TemporaryDirectory() as tmp:
        rt = make_runtime(tmp)
        rt.state.chat_id = "chat"
        rt.state.reload_agents(rt.team_file)
        enqueued = []
        advanced = []
        rt._enqueue = lambda *a, **k: enqueued.append((a, k))
        rt._advance_cursor = lambda: advanced.append(True)
        rt._download_image = lambda msg_id, key: os.path.join(tmp, "image.png")
        rt.handle_event({"message_id": "m-img", "chat_id": "chat", "sender_id": "user", "text": "", "image_key": "img-key"})
        deadline = time.time() + 2
        while not enqueued and time.time() < deadline:
            time.sleep(0.02)
        assert advanced == [True]
        assert enqueued
        assert enqueued[0][0][0] == "manager"
        assert "图片已下载" in enqueued[0][0][1]


def test_handle_event_passes_reply_metadata_to_deliver():
    with tempfile.TemporaryDirectory() as tmp:
        rt = make_runtime(tmp)
        rt.state.chat_id = "chat"
        captured = []
        rt._deliver = lambda *a, **k: captured.append((a, k))
        rt._advance_cursor = lambda: None
        rt._refresh_heartbeat = lambda: None
        rt.handle_event({
            "message_id": "om_msg",
            "chat_id": "chat",
            "sender_id": "user",
            "text": "hello",
            "parent_id": "om_parent",
            "root_id": "om_root",
        })
        assert captured
        assert captured[0][0] == ("manager", "hello", None, "om_msg", "om_parent", "om_root")


def test_deliver_prompt_contains_reply_metadata():
    with tempfile.TemporaryDirectory() as tmp:
        rt = make_runtime(tmp)
        calls = []
        with Patch(daemon, wake_on_deliver=lambda *a, **k: True):
            import claudeteam.runtime.tmux_utils as tmux_utils
            with Patch(tmux_utils, inject_when_idle=lambda *a, **k: calls.append((a, k)) or True):
                rt._deliver("manager", "hello", None, "om_msg", "om_parent", "om_root")
        assert calls
        prompt = calls[0][0][2]
        assert "Feishu message_id=om_msg" in prompt
        assert "parent_id=om_parent" in prompt
        assert "root_id=om_root" in prompt
        assert "--reply om_msg" in prompt


def test_router_state_bot_open_id_cached_and_lookup_degrades():
    st = RouterState()
    saved = []
    st.init_bot_id({"chat_id": "chat", "bot_open_id": "ou_cached"}, save_config=saved.append, lark_run=lambda *a, **k: None)
    assert st.bot_open_id == "ou_cached"
    assert saved == []

    st2 = RouterState()
    st2.init_bot_id({"chat_id": "chat"}, save_config=saved.append, lark_run=lambda *a, **k: None)
    assert st2.bot_open_id == ""

    st3 = RouterState()
    cfg = {"chat_id": "chat"}
    st3.init_bot_id(
        cfg,
        save_config=saved.append,
        lark_run=lambda *a, **k: {"items": [{"member_id_type": "open_id", "member_id": "ou_bot"}]},
    )
    assert st3.bot_open_id == "ou_bot"
    assert saved[-1]["bot_open_id"] == "ou_bot"


def main() -> int:
    tests = [v for k, v in sorted(globals().items()) if k.startswith("test_")]
    passed = failed = 0
    for fn in tests:
        try:
            fn()
            print(f"  ok {fn.__name__}")
            passed += 1
        except Exception as exc:
            print(f"  fail {fn.__name__}: {exc}")
            failed += 1
    print(f"\nrouter daemon runtime tests: {passed}/{passed + failed} passed")
    return 0 if failed == 0 else 1


if __name__ == "__main__":
    raise SystemExit(main())
