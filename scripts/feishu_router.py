#!/usr/bin/env python3
"""Thin compat shell — delegates to src/claudeteam/messaging/router.

All module-level names remain monkey-patchable (regression test compat).
CLI entry: python3 scripts/feishu_router.py [--stdin]
"""
import os, sys, json, time, atexit

_SCRIPT_DIR = os.path.dirname(__file__)
sys.path.insert(0, _SCRIPT_DIR)
_SRC_DIR = os.path.join(os.path.dirname(_SCRIPT_DIR), "src")
if _SRC_DIR not in sys.path:
    sys.path.insert(0, _SRC_DIR)

from claudeteam.messaging.router.state import RouterState
from claudeteam.messaging.router.cursor import load_cursor, save_cursor, refresh_heartbeat as _rh
from claudeteam.messaging.router.dispatch import classify_event, EventAction
from claudeteam.runtime.queue import enqueue_message, has_pending_messages, dequeue_pending, check_manager_unread
from claudeteam.runtime.paths import runtime_state_dir, legacy_script_state_file, ensure_parent
from claudeteam.messaging.renderer import render_inbox_text, render_tmux_prompt
from feishu_msg import _lark_run, cmd_say, sanitize_agent_message
from claudeteam.runtime.tmux_utils import inject_when_idle

_state = RouterState()
_TEAM_FILE = (os.environ.get("CLAUDETEAM_TEAM_FILE", "").strip()
              or os.path.join(os.path.dirname(_SCRIPT_DIR), "team.json"))

# Public path constants (patchable by tests).
# Computed via runtime_state_dir() (no mkdir side-effect) so import never
# touches the filesystem even when CLAUDETEAM_STATE_DIR is unset.
_sd = runtime_state_dir()
CURSOR_FILE = str(_sd / "router.cursor")
PID_FILE = str(_sd / "router.pid")
TMUX_INTERCEPT_LOG = str(_sd / "tmux_intercept.log")
ROUTER_MSG_DIR = str(_sd / "router_msgs")
LEGACY_CURSOR_FILE = legacy_script_state_file(".router.cursor")
LEGACY_PID_FILE = legacy_script_state_file(".router.pid")

# Keep private aliases for any code that referenced them directly
_CURSOR = CURSOR_FILE
_LEGACY_CURSOR = LEGACY_CURSOR_FILE


# ── cursor wrappers (module-level so tests can patch) ────────────────────────

def _refresh_heartbeat(): _rh(sys.modules[__name__].CURSOR_FILE)
def _load_cursor():
    _m = sys.modules[__name__]
    return load_cursor([_m.CURSOR_FILE, _m.LEGACY_CURSOR_FILE])
def _advance_cursor():
    _m = sys.modules[__name__]
    save_cursor(_m.CURSOR_FILE, time.time(), _m._load_cursor())
def _advance_cursor_to(ts):
    _m = sys.modules[__name__]
    save_cursor(_m.CURSOR_FILE, ts, _m._load_cursor())


# ── PID lock (module-level so tests can patch) ───────────────────────────────

def acquire_pid_lock():
    _m = sys.modules[__name__]
    os.makedirs(os.path.dirname(_m.PID_FILE), exist_ok=True)
    with open(_m.PID_FILE, "w") as f:
        f.write(str(os.getpid()))
    atexit.register(_m._cleanup_pid)

def _cleanup_pid():
    try:
        _m = sys.modules.get(__name__) or sys.modules.get("feishu_router")
        pid_file = _m.PID_FILE if _m else PID_FILE
        if os.path.exists(pid_file):
            with open(pid_file) as f:
                pid = int(f.read().strip())
            if pid == os.getpid():
                os.remove(pid_file)
    except Exception:
        pass


# ── wake (module-level so tests can patch) ───────────────────────────────────

def wake_on_deliver(agent_name):
    from claudeteam.messaging.router.wake import (
        wake_on_deliver as _wod, wait_cli_ui_ready, agent_has_live_cli)
    from claudeteam.cli_adapters import adapter_for_agent
    from config import TMUX_SESSION
    import claudeteam.runtime.tmux_utils as _tu
    _lsh = os.path.join(_SCRIPT_DIR, "lib", "agent_lifecycle.sh")
    return _wod(
        agent_name, _lsh,
        has_live_cli=lambda a: agent_has_live_cli(
            a, TMUX_SESSION,
            get_process_name=lambda n: adapter_for_agent(n).process_name()),
        wait_ready=lambda a, timeout_s=30: wait_cli_ui_ready(
            a,
            capture_pane_fn=lambda n: _tu.capture_pane(TMUX_SESSION, n),
            get_ready_markers=lambda n: adapter_for_agent(n).ready_markers(),
            get_process_name=lambda n: adapter_for_agent(n).process_name(),
            timeout_s=timeout_s),
    )


# ── delivery (module-level so tests can patch) ───────────────────────────────

def wake_agent(agent_name, message_preview, sender_agent=None, full_text=None, msg_id=""):
    _m = sys.modules[__name__]
    from config import TMUX_SESSION
    _m.wake_on_deliver(agent_name)
    if sender_agent:
        prompt = (f"【Router】你有来自 {sender_agent} 的新消息。\n"
                  f"执行: python3 scripts/feishu_msg.py inbox {agent_name}\n"
                  f"消息预览: {sanitize_agent_message(message_preview)[:500]}")
    else:
        content = render_inbox_text(sanitize_agent_message(full_text or message_preview))
        prompt = render_tmux_prompt("群聊消息", "用户在群里对你说:", content, agent_name)
    is_user_msg = (sender_agent is None)
    if not _m.has_pending_messages(agent_name):
        ok = _m.inject_when_idle(TMUX_SESSION, agent_name, prompt, wait_secs=15, force_after_wait=True)
        if ok:
            print(f"  → 已触发 {agent_name} 窗口（直接投递）")
            return
    _m.enqueue_message(agent_name, prompt, msg_id, is_user_msg=is_user_msg)
    print(f"  📥 消息已入队 {agent_name}")


# ── event handler (module-level, all deps via sys.modules for patchability) ──

def handle_event(event):
    _m = sys.modules[__name__]
    if _state.first_event_at is None:
        _state.first_event_at = time.time()
    _m._refresh_heartbeat()
    import slash_commands
    agents = _state.reload_agents(_TEAM_FILE)
    result = classify_event(
        event,
        is_seen=_state.is_seen,
        is_bot_message=_state.is_bot_message,
        chat_id=_state.chat_id,
        sanitize=sanitize_agent_message,
        parse_targets=lambda t: _state.parse_targets(t, agents),
        parse_sender=lambda t: _state.parse_sender(t, agents),
        is_slash=lambda t: slash_commands.dispatch(t)[0],
    )
    if result.action == EventAction.DROP:
        return
    _state.mark_seen(result.msg_id)
    if result.action == EventAction.SLASH:
        _, reply = slash_commands.dispatch(result.text)
        try:
            from feishu_msg import _lark_im_send, CHAT, build_system_card
            chat_id = CHAT()
            if chat_id:
                if isinstance(reply, dict) and reply.get("card"):
                    _lark_im_send(chat_id, card=reply["card"])
                else:
                    body = reply if isinstance(reply, str) else reply.get("text", "(空)")
                    _lark_im_send(chat_id, card=build_system_card(body))
        except Exception as e:
            print(f"  ⚠️ slash 回显失败: {e}")
        _m._advance_cursor()
        return
    print(f"[{time.strftime('%H:%M:%S')}] 新消息: {result.text[:500]}")
    for target in result.targets:
        print(f"  路由: {target} ← {result.sender or '用户'}")
        _m.wake_agent(target, result.text, sender_agent=result.sender,
                      full_text=result.text, msg_id=result.msg_id)
    _m._advance_cursor()


# ── history catchup (uses module-level _lark_run so tests can patch) ─────────

def _catchup_from_history(chat_id):
    _m = sys.modules[__name__]
    cursor = _m._load_cursor()
    if cursor is None:
        _m._advance_cursor()
        print("📥 首次启动，无 cursor，跳过历史补抓")
        return 0
    from datetime import datetime
    start_iso = datetime.fromtimestamp(cursor - 1).astimezone().isoformat(timespec="seconds")
    print(f"📥 历史补抓: 从 {start_iso} 开始拉错过的群聊消息")
    from claudeteam.messaging.router.cursor import parse_create_time
    from config import LARK_CLI
    fetched = replayed = 0
    page_token = ""
    deadline = time.time() + 30
    while time.time() < deadline:
        args = ["im", "+chat-messages-list", "--chat-id", chat_id,
                "--start", start_iso, "--sort", "asc",
                "--page-size", "50", "--as", "bot", "--format", "json"]
        if page_token:
            args += ["--page-token", page_token]
        data = _m._lark_run(args, timeout=40)
        if data is None:
            break
        max_ct = None
        for msg in data.get("messages", data.get("items", [])):
            fetched += 1
            ct_f = parse_create_time(msg.get("create_time"))
            if ct_f and (max_ct is None or ct_f > max_ct):
                max_ct = ct_f
            if msg.get("sender", {}).get("sender_type") != "user":
                continue
            if msg.get("msg_type") != "text":
                continue
            raw = msg.get("content") or msg.get("body", {}).get("content", "")
            try:
                text = json.loads(raw).get("text", "") if raw else ""
            except Exception:
                text = raw or ""
            ev = {"message_id": msg.get("message_id", ""), "chat_id": chat_id,
                  "sender_id": msg.get("sender", {}).get("id", ""),
                  "text": text, "message_type": "text"}
            try:
                handle_event(ev)
                replayed += 1
            except Exception as e:
                print(f"  ⚠️ replay 失败: {e}")
        if max_ct:
            _m._advance_cursor_to(max_ct)
        if not data.get("has_more"):
            break
        page_token = data.get("page_token", "")
        if not page_token:
            break
    print(f"📥 历史补抓完成: 拉取 {fetched} 条, replay {replayed} 条到 agent")
    return replayed


# ── CLI entry — delegates to daemon.main() ───────────────────────────────────

if __name__ == "__main__":
    from claudeteam.messaging.router.daemon import main
    main()
