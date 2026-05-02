"""`claudeteam router`

Long-running event subscriber: spawns `npx @larksuite/cli event +subscribe`
and feeds each NDJSON line into the routing loop.

Stops on Ctrl-C or when lark-cli exits.  Writes its PID to
state_dir/router.pid so the watchdog can supervise.
"""
from __future__ import annotations

import os
import signal
import subprocess
import sys

from claudeteam.feishu.subscribe import process_lines
from claudeteam.runtime import config, paths


def _build_subscribe_cmd(profile: str) -> list[str]:
    cmd = ["npx", "@larksuite/cli"]
    if profile:
        cmd += ["--profile", profile]
    cmd += [
        "event", "+subscribe",
        "--event-types", "im.message.receive_v1",
        "--compact",
        "--quiet",
        "--force",
        "--as", "bot",
    ]
    return cmd


def _write_pid_file() -> None:
    pf = paths.router_pid_file()
    pf.write_text(str(os.getpid()), encoding="utf-8")


def _cleanup_pid_file() -> None:
    try:
        pf = paths.router_pid_file()
        if pf.exists() and pf.read_text(encoding="utf-8").strip() == str(os.getpid()):
            pf.unlink()
    except Exception:
        pass


def main(argv: list[str]) -> int:
    if argv and argv[0] in ("-h", "--help"):
        print("usage: claudeteam router")
        return 0

    chat = config.chat_id()
    if not chat:
        print("❌ chat_id not set in runtime_config.json", file=sys.stderr)
        return 1

    team = config.load_team()
    agents = config.agent_names()
    if not agents:
        print("❌ team.json has no agents", file=sys.stderr)
        return 1

    _write_pid_file()
    signal.signal(signal.SIGTERM, lambda *_: sys.exit(0))

    profile = config.lark_profile()
    cmd = _build_subscribe_cmd(profile)
    print(f"🚀 router subscribing on chat {chat} (profile={profile or '<default>'})")

    try:
        proc = subprocess.Popen(
            cmd,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            text=True,
            bufsize=1,  # line-buffered
        )
    except FileNotFoundError:
        print("❌ npx / lark-cli not found in PATH", file=sys.stderr)
        _cleanup_pid_file()
        return 1

    try:
        if proc.stdout is None:
            print("❌ lark-cli started without stdout pipe", file=sys.stderr)
            return 1
        stats = process_lines(
            proc.stdout,
            team_agents=agents,
            chat_id=chat,
            default_target="manager",
        )
        print(f"router exited: handled={stats.handled} dropped={stats.dropped}")
        return 0 if proc.wait() == 0 else 1
    except KeyboardInterrupt:
        print("router stopped (Ctrl-C)")
        proc.terminate()
        return 0
    finally:
        _cleanup_pid_file()
