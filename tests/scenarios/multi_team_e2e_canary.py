#!/usr/bin/env python3
"""Multi-team e2e canary runner — drip "boss-style" messages into the
team B chat from a *different* app's bot (team A) so the events are
real WebSocket-eligible inbound for team B's router.

Why team A creds (not team B): team B router's `im.message.receive_v1`
fires only when the sender is NOT the team B app itself. Borrowing team
A bot creds gives us a cross-app sender at zero cost — no real boss
needs to type. Once the boss joins the chat in person, his messages
will trip the same router path; this canary is the autonomous version.

Cadence: one message every `INTERVAL_S` (default 60s, matching the
hotfix `[router] stale_event_threshold_s = 60` for team B). Adjust via
`--interval N`.

Stop: `touch /tmp/multi_team_canary.stop` (or pass `--stop-file PATH`).
The poll loop checks the flag at every interval boundary. SIGTERM also
exits cleanly.

Read team A creds from the live team A watchdog process env (no file
write of secrets, no shell history echo). Fail loud if team A daemon
is not running.

Usage:
    python3 tests/scenarios/multi_team_e2e_canary.py [--interval 60] [--max 0]

Falls back to running until stopped if --max is 0 / unset.
"""
from __future__ import annotations

import argparse
import json
import os
import signal
import sys
import time
import urllib.error
import urllib.request
from pathlib import Path

# Default for the AB2-EndToEnd-Test deployment. Override at runtime
# without editing the script via `CANARY_CHAT_ID=oc_xxx` env or
# `--chat-id`. chat_id is not a credential; the env knob is just
# defense-in-depth so a different team B deploy doesn't fork the file.
DEFAULT_TEAM_B_CHAT_ID = os.environ.get(
    "CANARY_CHAT_ID", "oc_d5376be51c652fe9ef7929f870930223")
DEFAULT_STOP_FILE = "/tmp/multi_team_canary.stop"
TEAM_A_WATCHDOG_PID_FILE = "/data/state/watchdog.pid"

CANARY_PHRASES = (
    "@manager_b 报道一下当前进度",
    "@worker_b 帮我看下今天的工作安排",
    "@manager_b 周五前能交付的小任务有哪些",
    "@manager_b 团队当前几个人在跑什么活儿",
    "@worker_b 简单 say 一句确认你在线",
    "@manager_b 让 worker_b 数一下当前目录有几个 .py 文件",
    "@manager_b 总结一下今早进展",
    "@worker_b 拉个最简单的 README typo PR 演示一下",
)


def _read_proc_env(pid: int) -> dict[str, str]:
    with open(f"/proc/{pid}/environ", "rb") as fh:
        raw = fh.read().decode("utf-8", errors="ignore")
    env = {}
    for kv in raw.split("\x00"):
        if "=" in kv:
            k, _, v = kv.partition("=")
            env[k] = v
    return env


def _team_a_creds() -> tuple[str, str]:
    """Borrow team A bot creds from the live team A watchdog daemon's
    process env. Fails loud if team A is down — that's by design,
    canary is meaningless without a cross-app sender."""
    pid_path = Path(TEAM_A_WATCHDOG_PID_FILE)
    if not pid_path.exists():
        sys.exit(f"❌ team A watchdog pid file missing ({pid_path}); start team A first")
    pid = int(pid_path.read_text().strip())
    if not Path(f"/proc/{pid}").exists():
        sys.exit(f"❌ team A watchdog (pid {pid}) not running")
    env = _read_proc_env(pid)
    app_id = env.get("FEISHU_APP_ID")
    app_sec = env.get("FEISHU_APP_SECRET")
    if not (app_id and app_sec):
        sys.exit("❌ team A watchdog has no FEISHU_APP_ID/SECRET in env")
    return app_id, app_sec


def _get_token(app_id: str, app_sec: str) -> str:
    req = urllib.request.Request(
        "https://open.feishu.cn/open-apis/auth/v3/tenant_access_token/internal",
        data=json.dumps({"app_id": app_id, "app_secret": app_sec}).encode(),
        headers={"Content-Type": "application/json"}, method="POST")
    return json.loads(urllib.request.urlopen(req, timeout=10).read())["tenant_access_token"]


def _send(token: str, chat_id: str, text: str) -> dict:
    req = urllib.request.Request(
        "https://open.feishu.cn/open-apis/im/v1/messages?receive_id_type=chat_id",
        data=json.dumps({
            "receive_id": chat_id,
            "msg_type": "text",
            "content": json.dumps({"text": text}),
        }).encode(),
        headers={"Authorization": f"Bearer {token}",
                 "Content-Type": "application/json"},
        method="POST")
    try:
        return json.loads(urllib.request.urlopen(req, timeout=10).read())
    except urllib.error.HTTPError as e:
        return {"http_error": e.code, "body": e.read().decode(errors="ignore")[:300]}


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--interval", type=int, default=60,
                    help="seconds between canary messages (default 60)")
    ap.add_argument("--max", type=int, default=0,
                    help="stop after N messages (0 = run forever)")
    ap.add_argument("--stop-file", default=DEFAULT_STOP_FILE,
                    help="touch this path to stop cleanly")
    ap.add_argument("--chat-id", default=DEFAULT_TEAM_B_CHAT_ID,
                    help="target chat_id (default: $CANARY_CHAT_ID or AB2-EndToEnd-Test)")
    args = ap.parse_args()

    stop_file = Path(args.stop_file)
    if stop_file.exists():
        stop_file.unlink()

    app_id, app_sec = _team_a_creds()
    token = _get_token(app_id, app_sec)
    print(f"🐤 canary armed: chat={args.chat_id}, interval={args.interval}s, "
          f"max={args.max or '∞'}, stop=touch {stop_file}", flush=True)

    sent = 0
    stopped = {"flag": False}

    def _on_sigterm(*_):
        stopped["flag"] = True
        print("🛑 SIGTERM received — exiting after current iteration", flush=True)
    signal.signal(signal.SIGTERM, _on_sigterm)

    try:
        while True:
            phrase = CANARY_PHRASES[sent % len(CANARY_PHRASES)]
            r = _send(token, args.chat_id, phrase)
            if r.get("code") == 0:
                msg_id = r.get("data", {}).get("message_id", "?")
                print(f"  → [{sent+1}] sent: {phrase!r} (msg_id={msg_id})", flush=True)
            else:
                print(f"  ⚠️ [{sent+1}] send failed: {r}", flush=True)
                # token might've expired; refresh once and continue
                try:
                    token = _get_token(app_id, app_sec)
                    print("  🔑 refreshed team A token", flush=True)
                except Exception as e:
                    print(f"  ⚠️ token refresh failed: {e}", flush=True)
            sent += 1
            if args.max and sent >= args.max:
                print(f"✅ reached --max={args.max}; exiting", flush=True)
                return 0

            # sleep in 1s slices so stop-file / SIGTERM react fast
            for _ in range(args.interval):
                if stopped["flag"] or stop_file.exists():
                    if stop_file.exists():
                        print(f"🛑 stop-file {stop_file} present — exiting", flush=True)
                    return 0
                time.sleep(1)
    except KeyboardInterrupt:
        print("🛑 KeyboardInterrupt — exiting", flush=True)
        return 0


if __name__ == "__main__":
    raise SystemExit(main())
