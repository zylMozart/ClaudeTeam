"""Router daemon: PID lock, background loops, and main entry point.

This module wires together state/cursor/dispatch/wake into a running daemon.
It owns all subprocess and threading side-effects that are not suitable for
unit testing.

Entry point: main()
"""
from __future__ import annotations

import atexit
import json
import os
import signal
import subprocess
import sys
import threading
import time
from typing import Optional

from claudeteam.messaging.router.state import RouterState
from claudeteam.messaging.router.cursor import (
    load_cursor, save_cursor, refresh_heartbeat, parse_create_time,
)
from claudeteam.messaging.router.dispatch import classify_event, EventAction
from claudeteam.messaging.router.wake import wake_on_deliver, wait_cli_ui_ready, agent_has_live_cli


def _build_router(cfg: dict, team_file: str, scripts_dir: str) -> "_RouterRuntime":
    """Construct a fully-wired RouterRuntime from loaded config."""
    return _RouterRuntime(cfg=cfg, team_file=team_file, scripts_dir=scripts_dir)


class _RouterRuntime:
    """Wires all router dependencies together for a single daemon run."""

    def __init__(self, cfg: dict, team_file: str, scripts_dir: str) -> None:
        from claudeteam.runtime.paths import runtime_state_file, legacy_script_state_file, ensure_parent
        from claudeteam.runtime.queue import enqueue_message, has_pending_messages, dequeue_pending, check_manager_unread
        from claudeteam.messaging.renderer import render_inbox_text, render_tmux_prompt
        from claudeteam.cli_adapters import adapter_for_agent

        self.state = RouterState()
        self.state.chat_id = cfg.get("chat_id", "")
        self.team_file = team_file
        self.scripts_dir = scripts_dir
        self.lark_cli = cfg.get("_lark_cli", [])
        self.tmux_session = cfg.get("_tmux_session", "ClaudeTeam")
        self.lifecycle_sh = os.path.join(scripts_dir, "lib", "agent_lifecycle.sh")
        self.images_dir = cfg.get("_images_dir", "/tmp/images")
        self.cursor_file = runtime_state_file("router.cursor")
        self.legacy_cursor_file = legacy_script_state_file(".router.cursor")
        self.pid_file = runtime_state_file("router.pid")
        self.legacy_pid_file = legacy_script_state_file(".router.pid")
        self.tmux_intercept_log = runtime_state_file("tmux_intercept.log")
        self.router_msg_dir = runtime_state_file("router_messages")
        self._ensure_parent = ensure_parent
        self._enqueue = enqueue_message
        self._has_pending = has_pending_messages
        self._dequeue = dequeue_pending
        self._check_unread = check_manager_unread
        self._render_inbox = render_inbox_text
        self._render_tmux = render_tmux_prompt
        self._adapter = adapter_for_agent

    # ── cursor helpers ────────────────────────────────────────────

    def _load_cursor(self) -> Optional[float]:
        return load_cursor([self.cursor_file, self.legacy_cursor_file])

    def _advance_cursor(self) -> None:
        cur = self._load_cursor()
        save_cursor(self.cursor_file, time.time(), cur)

    def _advance_cursor_to(self, ts: float) -> None:
        cur = self._load_cursor()
        save_cursor(self.cursor_file, ts, cur)

    def _refresh_heartbeat(self) -> None:
        refresh_heartbeat(self.cursor_file, _save_fn=lambda p, t: save_cursor(p, t))

    # ── event handling ────────────────────────────────────────────

    def handle_event(self, event: dict) -> None:
        """Phase 1 (unconditional heartbeat) + Phase 2 (filter/route)."""
        if self.state.first_event_at is None:
            self.state.first_event_at = time.time()
        self._refresh_heartbeat()

        agents = self.state.reload_agents(self.team_file)

        import slash_commands  # scripts-layer, available via sys.path
        import tmux_command
        import team_command
        from feishu_msg import _lark_run, cmd_say, sanitize_agent_message, _lark_im_send, CHAT, build_system_card

        result = classify_event(
            event,
            is_seen=self.state.is_seen,
            is_bot_message=self.state.is_bot_message,
            chat_id=self.state.chat_id,
            sanitize=sanitize_agent_message,
            parse_targets=lambda t: self.state.parse_targets(t, agents),
            parse_sender=lambda t: self.state.parse_sender(t, agents),
            is_slash=lambda t: slash_commands.dispatch(t)[0],
        )

        if result.action == EventAction.DROP:
            return

        self.state.mark_seen(result.msg_id)

        if result.action == EventAction.SLASH:
            _, reply = slash_commands.dispatch(result.text)
            self._handle_slash_reply(result, reply, cmd_say, _lark_im_send, CHAT, build_system_card)
            self._advance_cursor()
            return

        # ROUTE
        print(f"[{time.strftime('%H:%M:%S')}] 新消息: {result.text[:500]}")
        for target in result.targets:
            print(f"  路由: {target} ← {result.sender or '用户'}")
            self._deliver(target, result.text, result.sender, result.msg_id)
        self._advance_cursor()

    def _handle_slash_reply(self, result, reply, cmd_say, _lark_im_send, CHAT, build_system_card):
        first = result.text.strip().split()[0] if result.text.strip() else ""
        ts = time.strftime('%Y-%m-%d %H:%M:%S')
        line = f"[{ts}] slash {first} msg_id={result.msg_id} → 群聊回显(无 agent 介入)"
        print(line)
        try:
            self._ensure_parent(self.tmux_intercept_log)
            with open(self.tmux_intercept_log, "a") as f:
                f.write(line + "\n")
        except Exception:
            pass
        try:
            chat_id = CHAT()
            if not chat_id:
                print("  ⚠️ chat_id 未配置,无法回显")
            elif isinstance(reply, dict) and reply.get("card"):
                _lark_im_send(chat_id, card=reply["card"])
            else:
                body = reply if isinstance(reply, str) else (
                    reply.get("text", "(空)") if isinstance(reply, dict) else "(空)")
                if first == "/tmux":
                    body = f"```\n{body}\n```"
                _lark_im_send(chat_id, card=build_system_card(body))
        except Exception as e:
            print(f"  ⚠️ slash 回显失败: {e}")

    def _deliver(self, agent: str, text: str, sender: Optional[str], msg_id: str) -> None:
        """Deliver a routed message to one agent (wake if needed, then inject or enqueue)."""
        from feishu_msg import sanitize_agent_message
        text = sanitize_agent_message(text)

        wake_on_deliver(
            agent, self.lifecycle_sh,
            has_live_cli=lambda a: agent_has_live_cli(
                a, self.tmux_session,
                get_process_name=lambda n: self._adapter(n).process_name(),
            ),
            wait_ready=lambda a, timeout_s=30: wait_cli_ui_ready(
                a,
                capture_pane_fn=lambda n: _capture_pane(self.tmux_session, n),
                get_ready_markers=lambda n: self._adapter(n).ready_markers(),
                get_process_name=lambda n: self._adapter(n).process_name(),
                timeout_s=timeout_s,
            ),
        )

        if sender:
            from claudeteam.messaging.router._tpl import TPL_AGENT_NOTIFY
            prompt = TPL_AGENT_NOTIFY.format(sender=sender, agent=agent, preview=text[:500])
        else:
            content = self._render_inbox(text)
            if len(content) > 400:
                msg_file = os.path.join(self.router_msg_dir, f"router_msg_{agent}.txt")
                os.makedirs(os.path.dirname(msg_file), exist_ok=True)
                with open(msg_file, "w", encoding="utf-8") as f:
                    f.write(content)
                from claudeteam.messaging.router._tpl import TPL_USER_MSG_LONG
                prompt = TPL_USER_MSG_LONG.format(
                    file_path=msg_file, preview=self._render_inbox(content[:200]), agent=agent)
            else:
                prompt = self._render_tmux("群聊消息", "用户在群里对你说:", content, agent)

        from tmux_utils import inject_when_idle
        is_user_msg = (sender is None)
        has_pending = self._has_pending(agent)
        if not has_pending:
            ok = inject_when_idle(self.tmux_session, agent, prompt, wait_secs=15, force_after_wait=True)
            if ok:
                print(f"  → 已触发 {agent} 窗口（直接投递）")
                return
        self._enqueue(agent, prompt, msg_id, is_user_msg=is_user_msg)
        label = "队列有积压，保证 FIFO" if has_pending else "agent 忙碌，等待投递"
        print(f"  📥 消息已入队 {agent}（{label}）")

    # ── background loops ──────────────────────────────────────────

    def queue_delivery_loop(self) -> None:
        last_unread_check = 0
        while True:
            try:
                for agent in self.state.reload_agents(self.team_file):
                    self._dequeue(agent)
                last_unread_check = self._check_unread(last_unread_check)
            except Exception as e:
                print(f"  ⚠️ 队列投递异常: {e}")
            time.sleep(3)

    def catchup_from_history(self, chat_id: str) -> int:
        from feishu_msg import _lark_run
        cursor = self._load_cursor()
        if cursor is None:
            self._advance_cursor()
            print("📥 首次启动，无 cursor，跳过历史补抓")
            return 0

        from datetime import datetime
        start_iso = datetime.fromtimestamp(cursor - 1).astimezone().isoformat(timespec="seconds")
        print(f"📥 历史补抓: 从 {start_iso} 开始拉错过的群聊消息")

        fetched = replayed = 0
        page_token = ""
        deadline = time.time() + 30
        while time.time() < deadline:
            args = ["im", "+chat-messages-list", "--chat-id", chat_id,
                    "--start", start_iso, "--sort", "asc",
                    "--page-size", "50", "--as", "bot", "--format", "json"]
            if page_token:
                args += ["--page-token", page_token]
            try:
                data = _lark_run(args, timeout=40)
            except subprocess.TimeoutExpired:
                print("  ⚠️ 历史补抓超时，放弃剩余页")
                break
            if data is None:
                print(f"  ⚠️ lark-cli 调用失败: 历史补抓 {chat_id} (停止本轮)", file=sys.stderr)
                break
            max_ct: Optional[float] = None
            for m in data.get("messages", data.get("items", [])):
                fetched += 1
                ct = m.get("create_time")
                if ct:
                    ct_f = parse_create_time(ct)
                    if ct_f is not None and (max_ct is None or ct_f > max_ct):
                        max_ct = ct_f
                if m.get("sender", {}).get("sender_type") != "user":
                    continue
                if m.get("msg_type") != "text":
                    continue
                raw = m.get("content") or m.get("body", {}).get("content", "")
                try:
                    text = json.loads(raw).get("text", "") if raw else ""
                except (json.JSONDecodeError, AttributeError, TypeError):
                    text = raw or ""
                ev = {"message_id": m.get("message_id", ""), "chat_id": chat_id,
                      "sender_id": m.get("sender", {}).get("id", ""),
                      "text": text, "message_type": "text"}
                try:
                    self.handle_event(ev)
                    replayed += 1
                except Exception as e:
                    print(f"  ⚠️ replay 事件失败: {e}")
            if max_ct is not None:
                self._advance_cursor_to(max_ct)
            if not data.get("has_more"):
                break
            page_token = data.get("page_token", "")
            if not page_token:
                break

        if time.time() >= deadline:
            print("  ⚠️ 历史补抓达到 30s 硬上限，剩余页放弃")
        print(f"📥 历史补抓完成: 拉取 {fetched} 条, replay {replayed} 条到 agent")
        return replayed


def _capture_pane(session: str, agent: str) -> str:
    try:
        import subprocess as _sp
        r = _sp.run(["tmux", "capture-pane", "-t", f"{session}:{agent}", "-p", "-S", "-30"],
                    capture_output=True, text=True, timeout=5)
        return r.stdout if r.returncode == 0 else ""
    except Exception:
        return ""


def _pid_file_is_live_router(path: str) -> bool:
    if not os.path.exists(path):
        return False
    try:
        with open(path) as f:
            old_pid = int(f.read().strip())
        os.kill(old_pid, 0)
        with open(f"/proc/{old_pid}/cmdline", "rb") as f:
            cmdline = f.read().decode("utf-8", errors="ignore")
        return "feishu_router.py" in cmdline
    except (ValueError, OSError):
        return False


def main() -> None:
    """Wire up all dependencies and run the router event loop."""
    # Ensure scripts/ is in sys.path for scripts-layer imports
    _scripts = os.path.join(os.path.dirname(__file__), *(['..'] * 4), 'scripts')
    _scripts = os.path.normpath(_scripts)
    if _scripts not in sys.path:
        sys.path.insert(0, _scripts)

    from config import AGENTS, TMUX_SESSION, PROJECT_ROOT, load_runtime_config, LARK_CLI
    from claudeteam.runtime.paths import runtime_state_file, legacy_script_state_file

    cfg_data = load_runtime_config()
    chat_id = cfg_data.get("chat_id", "")
    if not chat_id:
        print("❌ chat_id 未配置，请先运行 setup.py")
        sys.exit(1)

    team_file = (os.environ.get("CLAUDETEAM_TEAM_FILE", "").strip()
                 or os.path.join(PROJECT_ROOT, "team.json"))
    scripts_dir = os.path.join(PROJECT_ROOT, "scripts")

    cfg_data["_lark_cli"] = LARK_CLI
    cfg_data["_tmux_session"] = TMUX_SESSION
    cfg_data["_images_dir"] = os.path.join(PROJECT_ROOT, "workspace", "shared", "images")
    runtime = _build_router(cfg_data, team_file, scripts_dir)

    pid_file = runtime_state_file("router.pid")
    legacy_pid_file = legacy_script_state_file(".router.pid")
    for pf in (pid_file, legacy_pid_file):
        if _pid_file_is_live_router(pf):
            with open(pf) as f:
                old_pid = f.read().strip()
            print(f"❌ Router 已在运行 (PID {old_pid})，请勿重复启动")
            sys.exit(1)
    with open(pid_file, "w") as f:
        f.write(str(os.getpid()))

    def _cleanup_pid():
        try:
            if os.path.exists(pid_file):
                with open(pid_file) as f:
                    pid = int(f.read().strip())
                if pid == os.getpid():
                    os.remove(pid_file)
        except Exception:
            pass

    atexit.register(_cleanup_pid)
    signal.signal(signal.SIGTERM, lambda *_: sys.exit(0))

    print("🚀 Router Daemon 启动 (messaging/router)")
    runtime._refresh_heartbeat()
    print(f"💬 监听群组: {chat_id}")
    print(f"👥 Agent 列表: {', '.join(runtime.state.reload_agents(team_file))}")

    threading.Thread(target=runtime.queue_delivery_loop, daemon=True).start()
    threading.Thread(target=lambda: runtime.catchup_from_history(chat_id), daemon=True).start()

    def _poll_loop():
        while True:
            try:
                runtime.catchup_from_history(chat_id)
                runtime._refresh_heartbeat()
            except Exception as e:
                print(f"  ⚠️ 轮询 catchup 异常: {e}")
            time.sleep(5)
    threading.Thread(target=_poll_loop, daemon=True).start()

    stdin_mode = "--stdin" in sys.argv
    if stdin_mode:
        print("📡 模式: stdin 事件流")
        for line in sys.stdin:
            line = line.strip()
            if not line:
                continue
            try:
                runtime.handle_event(json.loads(line))
            except json.JSONDecodeError:
                print(f"  ⚠️ 无法解析事件: {line[:100]}")
            except Exception as e:
                print(f"  ⚠️ 事件处理异常: {e}")
    else:
        print("📡 模式: 自启 lark-cli event")
        proc = subprocess.Popen(
            LARK_CLI + ["event", "+subscribe", "--event-types", "im.message.receive_v1",
                        "--compact", "--quiet", "--force", "--as", "bot"],
            stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
        try:
            for line in proc.stdout:
                line = line.strip()
                if not line:
                    continue
                try:
                    runtime.handle_event(json.loads(line))
                except json.JSONDecodeError:
                    print(f"  ⚠️ 无法解析事件: {line[:100]}")
                except Exception as e:
                    print(f"  ⚠️ 事件处理异常: {e}")
        except KeyboardInterrupt:
            proc.terminate()
        finally:
            proc.terminate()
