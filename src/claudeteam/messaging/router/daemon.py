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

        from claudeteam.commands.slash.standalone import dispatch as _slash_dispatch, is_slash_command
        from claudeteam.integrations.feishu.client import _lark_im_send, get_chat_id
        from claudeteam.messaging.service import sanitize_agent_message, build_system_card

        result = classify_event(
            event,
            is_seen=self.state.is_seen,
            is_bot_message=self.state.is_bot_message,
            chat_id=self.state.chat_id,
            sanitize=sanitize_agent_message,
            parse_targets=lambda t: self.state.parse_targets(t, agents),
            parse_sender=lambda t: self.state.parse_sender(t, agents),
            is_slash=is_slash_command,
        )

        if result.action == EventAction.DROP:
            if result.reason == "empty_text" and event.get("image_key"):
                # 图片：保留 pre-mark；下载是异步线程，重复触发要避免。
                self.state.mark_seen(result.msg_id)
                self._handle_image(event, "", agents)
                self._advance_cursor()
            return

        self._handle_image(event, result.text, agents)

        if result.action == EventAction.SLASH:
            _, reply = _slash_dispatch(result.text)
            self._handle_slash_reply(result, reply, _lark_im_send, get_chat_id, build_system_card)
            # SLASH 回显完成后再 mark_seen；否则 _handle_slash_reply 抛异常时
            # catchup 还能重新处理。
            self.state.mark_seen(result.msg_id)
            self._advance_cursor()
            return

        # ROUTE — mark_seen 必须在 _deliver 真正完成后再调，否则 ws 闪断 +
        # 投递异常时 message 会被 is_seen 去重逻辑吞掉，catchup_from_history
        # 也无法恢复（manager 的「拉取 N 条 / replay 0」吞消息现象）。
        print(f"[{time.strftime('%H:%M:%S')}] 新消息: {result.text[:500]}")
        delivered = 0
        for target in result.targets:
            print(f"  路由: {target} ← {result.sender or '用户'}")
            try:
                self._deliver(target, result.text, result.sender, result.msg_id,
                              result.parent_id, result.root_id)
                delivered += 1
            except Exception as exc:
                # 单 target 异常不阻塞其他 target；msg_id 暂不进 seen 集，下轮
                # ws 重传 / catchup 可重试整条事件。_enqueue 内部按 msg_id 幂等，
                # 不会对成功 target 双投；inject 不幂等，下文有保护。
                print(f"  ⚠️ 投递 {target} 异常 (msg_id={result.msg_id[:16]}…): {exc}")

        if delivered == 0:
            # 全军覆没 → 不 mark_seen / 不 advance cursor，留给下次重试。
            print(f"  ⚠️ msg_id={result.msg_id[:16]}… 全部 {len(result.targets)} 个 target 投递失败，"
                  f"留待 ws 重传或 catchup 重试")
            return

        # 至少一个 target 成功（inject 或入队 _enqueue 完成）→ mark_seen，避免
        # 已成功 target 在重传时被 inject 重复触发（_enqueue 幂等没问题，inject
        # 不是；这里宁可放弃对失败 target 的重试也保护成功 target 不被双投）。
        self.state.mark_seen(result.msg_id)
        self._advance_cursor()

        is_boss_msg = result.sender is None and "manager" in result.targets
        if is_boss_msg:
            self._maybe_trigger_reflection()

    def _handle_image(self, event: dict, text: str, agents: list[str]) -> None:
        image_key = event.get("image_key", "")
        msg_id = event.get("message_id", "")
        if not image_key or not msg_id:
            return

        def _download_async() -> None:
            path = self._download_image(msg_id, image_key)
            if not path:
                return
            targets = self.state.parse_targets(text, agents) if text else []
            agent = targets[0] if targets else "manager"
            from claudeteam.messaging.router._tpl import TPL_IMAGE_DOWNLOADED
            self._enqueue(agent, TPL_IMAGE_DOWNLOADED.format(path=path), f"{msg_id}_img", is_user_msg=True)

        threading.Thread(target=_download_async, daemon=True).start()

    def _download_image(self, message_id: str, file_key: str) -> Optional[str]:
        os.makedirs(self.images_dir, exist_ok=True)
        output_name = f"{int(time.time())}_{message_id[:8]}_{file_key[:8]}"
        r = subprocess.run(
            self.lark_cli + ["im", "+messages-resources-download",
                             "--message-id", message_id,
                             "--file-key", file_key,
                             "--type", "image",
                             "--output", output_name,
                             "--as", "bot"],
            capture_output=True, text=True, timeout=30, cwd=self.images_dir,
        )
        if r.returncode != 0:
            print(f"  ⚠️ 图片下载失败: {r.stderr.strip()[:100]}")
            return None
        for name in os.listdir(self.images_dir):
            if name.startswith(output_name):
                path = os.path.join(self.images_dir, name)
                print(f"  📥 图片已保存: {path}")
                return path
        return None

    def _handle_slash_reply(self, result, reply, _lark_im_send, get_chat_id, build_system_card):
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
            chat_id = get_chat_id()
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

    def _deliver(
        self,
        agent: str,
        text: str,
        sender: Optional[str],
        msg_id: str,
        parent_id: str = "",
        root_id: str = "",
    ) -> None:
        """Deliver a routed message to one agent (wake if needed, then inject or enqueue)."""
        from claudeteam.messaging.service import sanitize_agent_message
        text = sanitize_agent_message(text)
        ref_lines = [
            "",
            f"Feishu message_id={msg_id}",
            f"如需引用回复，使用: python3 scripts/feishu_msg.py say {agent} \"回复内容\" --reply {msg_id}",
        ]
        if parent_id:
            ref_lines.insert(2, f"parent_id={parent_id}")
        if root_id:
            ref_lines.insert(3 if parent_id else 2, f"root_id={root_id}")
        ref_context = "\n".join(ref_lines)

        wake_ready = wake_on_deliver(
            agent, self.lifecycle_sh,
            has_live_cli=lambda a: agent_has_live_cli(
                a, self.tmux_session,
                get_process_name=lambda n: self._adapter(n).process_name(),
                # Stage 2: pass the wrapper-tolerant set (claude+node etc.).
                get_process_names=lambda n: self._adapter(n).process_names(),
            ),
            wait_ready=lambda a, timeout_s=30: wait_cli_ui_ready(
                a,
                capture_pane_fn=lambda n: _capture_pane(self.tmux_session, n),
                get_ready_markers=lambda n: self._adapter(n).ready_markers(),
                get_process_name=lambda n: self._adapter(n).process_name(),
                get_process_names=lambda n: self._adapter(n).process_names(),
                tmux_session=self.tmux_session,
                timeout_s=timeout_s,
            ),
        )

        if sender:
            from claudeteam.messaging.router._tpl import TPL_AGENT_NOTIFY
            prompt = TPL_AGENT_NOTIFY.format(sender=sender, agent=agent, preview=(text + ref_context)[:800])
        else:
            content = self._render_inbox(text + ref_context)
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

        from claudeteam.runtime.tmux_utils import inject_when_idle
        is_user_msg = (sender is None)
        has_pending = self._has_pending(agent)
        if not wake_ready:
            self._enqueue(agent, prompt, msg_id, is_user_msg=is_user_msg)
            print(f"  📥 wake 失败，消息已入队 {agent}（等待下次 ready 后投递）")
            return
        if agent == "manager" and is_user_msg:
            ok = inject_when_idle(
                self.tmux_session, agent, prompt,
                wait_secs=3, force_after_wait=True,
                submit_keys=self._adapter(agent).submit_keys(),
            )
            if ok:
                print(f"  → 已触发 {agent} 窗口（老板消息实时投递）")
                return
            detail = getattr(ok, "error", "") or "not submitted"
            print(f"  📥 实时投递失败 {agent}: {detail}，转入队列")
        elif not has_pending:
            ok = inject_when_idle(
                self.tmux_session, agent, prompt,
                wait_secs=30, force_after_wait=False,
                submit_keys=self._adapter(agent).submit_keys(),
            )
            if ok:
                print(f"  → 已触发 {agent} 窗口（直接投递）")
                return
            detail = getattr(ok, "error", "") or "not submitted"
            print(f"  📥 直接投递未提交 {agent}: {detail}，转入队列")
        self._enqueue(agent, prompt, msg_id, is_user_msg=is_user_msg)
        label = "队列有积压，保证 FIFO" if has_pending else "agent 忙碌，等待投递"
        print(f"  📥 消息已入队 {agent}（{label}）")

    # ── reflection meeting ─────────────────────────────────────────

    def _maybe_trigger_reflection(self) -> None:
        from claudeteam.messaging.router.reflection import increment_and_check, reset_counter, build_reflection_prompt, load_counter
        from claudeteam.runtime.paths import runtime_state_dir
        state_dir = str(runtime_state_dir())
        if not increment_and_check(state_dir):
            return
        counter_data = load_counter(state_dir)
        msg_count = counter_data["count"]
        print(f"[reflection] 老板消息计数达到 {msg_count}，检查是否可以开反思大会")
        try:
            from claudeteam.runtime.agent_state import classify
            agents = self.state.reload_agents(self.team_file)
            non_manager = [a for a in agents if a != "manager"]
            busy = []
            for agent in non_manager:
                pane = _capture_pane(self.tmux_session, agent)
                st = classify(pane)
                if st.get("code") in ("busy", "permission_wait"):
                    busy.append(agent)
            if busy:
                print(f"[reflection] 有 agent 忙碌中 ({busy})，延后反思大会")
                return
        except Exception as e:
            print(f"[reflection] agent 状态检查失败: {e}，延后反思大会")
            return
        reset_counter(state_dir)
        print(f"[reflection] 全员空闲，触发反思大会（{msg_count} 条消息后）")
        from claudeteam.runtime.tmux_utils import inject_when_idle
        for agent in non_manager:
            prompt = build_reflection_prompt(agent, msg_count)
            try:
                inject_when_idle(
                    self.tmux_session, agent, prompt,
                    wait_secs=5, force_after_wait=False,
                    submit_keys=self._adapter(agent).submit_keys(),
                )
            except Exception:
                pass
        manager_prompt = (
            f"【反思大会已触发】已处理 {msg_count} 条老板消息，已向 {len(non_manager)} 名员工发送反思提示。\n"
            f"请等待员工提交反思，然后汇总发给老板。"
        )
        try:
            inject_when_idle(
                self.tmux_session, "manager", manager_prompt,
                wait_secs=3, force_after_wait=False,
                submit_keys=self._adapter("manager").submit_keys(),
            )
        except Exception:
            pass

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
        from claudeteam.integrations.feishu.client import _lark_run
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
                      "text": text, "message_type": "text",
                      "parent_id": m.get("parent_id", ""),
                      "root_id": m.get("root_id", "")}
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
        return "feishu_router.py" in cmdline or "claudeteam.messaging.router.daemon" in cmdline
    except (ValueError, OSError):
        return False


def main() -> None:
    """Wire up all dependencies and run the router event loop."""
    from claudeteam.runtime.config import AGENTS, TMUX_SESSION, PROJECT_ROOT, load_runtime_config, save_runtime_config, LARK_CLI
    from claudeteam.runtime.paths import runtime_state_file, legacy_script_state_file, ensure_parent

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
    pid_str = str(os.getpid())
    for pf in (pid_file, legacy_pid_file):
        ensure_parent(pf)
        with open(pf, "w") as f:
            f.write(pid_str)

    def _cleanup_pid():
        try:
            my_pid = os.getpid()
            for pf in (pid_file, legacy_pid_file):
                if os.path.exists(pf):
                    with open(pf) as f:
                        pid = int(f.read().strip())
                    if pid == my_pid:
                        os.remove(pf)
        except Exception:
            pass

    atexit.register(_cleanup_pid)
    signal.signal(signal.SIGTERM, lambda *_: sys.exit(0))

    print("🚀 Router Daemon 启动 (messaging/router)")
    runtime._refresh_heartbeat()
    from claudeteam.integrations.feishu.client import _lark_run
    def _save_public_runtime_config(cfg: dict) -> None:
        save_runtime_config({k: v for k, v in cfg.items() if not k.startswith("_")})
    runtime.state.init_bot_id(cfg_data, save_config=_save_public_runtime_config, lark_run=_lark_run)
    print(f"💬 监听群组: {chat_id}")
    print(f"👥 Agent 列表: {', '.join(runtime.state.reload_agents(team_file))}")

    threading.Thread(target=runtime.queue_delivery_loop, daemon=True).start()
    threading.Thread(target=lambda: runtime.catchup_from_history(chat_id), daemon=True).start()

    # 独立心跳线程 — 跟事件流 / catchup poll / Bitable 调用完全解耦。
    # 旧路径只在事件成功路由 (L73/L77) 或 catchup poll (L541) 时刷 cursor mtime,
    # 任何一处卡 (Bitable 限流 / 网络 / WebSocket 沉默) 都会让 mtime 停滞 → watchdog
    # 错过故障窗口最长 5 分钟。新心跳线程 30s/拍 touch mtime, watchdog 端配阈值
    # 90s = 3 拍漏判即视为不健康,触发重启。
    # daemon=True: 主进程退出时线程随之结束,不留 zombie。
    # 设计文档: workspace/architect/router_autoheal_design_2026-04-30.md §2.1
    HEARTBEAT_INTERVAL = int(os.environ.get("ROUTER_HEARTBEAT_INTERVAL", 30))

    def _heartbeat_loop():
        while True:
            try:
                runtime._refresh_heartbeat()
            except Exception as e:
                print(f"  ⚠️ heartbeat 异常: {e}")
            time.sleep(HEARTBEAT_INTERVAL)

    threading.Thread(target=_heartbeat_loop, daemon=True).start()

    def _poll_loop():
        last_replay_time = time.time()
        warned = False
        while True:
            try:
                n = runtime.catchup_from_history(chat_id)
                if n > 0:
                    last_replay_time = time.time()
                    warned = False
                runtime._refresh_heartbeat()
                if (not warned
                        and runtime.state.first_event_at is None
                        and time.time() - last_replay_time > 300):
                    print("  ⚠️ 5 分钟内 WebSocket/catchup 都没有 replay 新消息，若群里有消息请检查事件订阅")
                    warned = True
            except Exception as e:
                print(f"  ⚠️ 轮询 catchup 异常: {e}")
            time.sleep(int(os.environ.get("CATCHUP_POLL_INTERVAL", 30)))
    threading.Thread(target=_poll_loop, daemon=True).start()

    def _event_watchdog():
        time.sleep(45)
        if runtime.state.first_event_at is None:
            print("=" * 60)
            print("⚠️ Router 启动 45 秒内未收到任何 Feishu 事件")
            print("   如果群里发消息没反应，请检查 App 事件订阅 im.message.receive_v1")
            print("   可运行: python3 scripts/setup.py 重新完成事件订阅")
            print("=" * 60)
    threading.Thread(target=_event_watchdog, daemon=True).start()

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
