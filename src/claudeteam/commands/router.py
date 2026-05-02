"""`claudeteam router`

Long-running event subscriber: spawns `npx @larksuite/cli event +subscribe`
and feeds each NDJSON line into the routing loop.

Stops on Ctrl-C or when lark-cli exits.  Writes its PID to
state_dir/router.pid so the watchdog can supervise.
"""
from __future__ import annotations

import signal
import subprocess
import sys

from claudeteam.feishu import catchup
from claudeteam.feishu.deliver import apply as _deliver_apply
from claudeteam.feishu.subscribe import process_lines
from claudeteam.runtime import config, paths, pidlock, wake


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


def _apply_with_wake(decision):
    """Production deliver wrapper: lazy-wake panes before injecting."""
    return _deliver_apply(decision, wake_fn=wake.wake_if_dormant)


def _on_progress(decision, stats):
    """After each handled event, advance the catchup cursor."""
    catchup.record_decision(decision)


def main(argv: list[str]) -> int:
    if argv and argv[0] in ("-h", "--help"):
        print("usage: claudeteam router")
        return 0

    chat = config.chat_id()
    if not chat:
        print("❌ chat_id not set in runtime_config.json", file=sys.stderr)
        return 1

    agents = config.agent_names()
    if not agents:
        print("❌ team.json has no agents", file=sys.stderr)
        return 1

    pid_file = paths.router_pid_file()
    if not pidlock.acquire(pid_file, name="router"):
        return 1
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
        pidlock.release(pid_file)
        return 1

    try:
        if proc.stdout is None:
            print("❌ lark-cli started without stdout pipe", file=sys.stderr)
            return 1

        loop_kwargs = dict(
            team_agents=agents,
            chat_id=chat,
            default_target="manager",
            apply_fn=_apply_with_wake,
            on_progress=_on_progress,
        )

        # Catchup: replay anything newer than the cursor before going live
        try:
            pending = catchup.pending_lines(chat, profile=profile)
        except Exception as e:
            print(f"⚠️  catchup fetch failed: {e}", file=sys.stderr)
            pending = []
        if pending:
            print(f"📥 catching up {len(pending)} missed message(s)")
            process_lines(iter(pending), **loop_kwargs)

        stats = process_lines(proc.stdout, **loop_kwargs)
        print(f"router exited: handled={stats.handled} dropped={stats.dropped}")
        return 0 if proc.wait() == 0 else 1
    except KeyboardInterrupt:
        print("router stopped (Ctrl-C)")
        proc.terminate()
        return 0
    finally:
        pidlock.release(pid_file)
