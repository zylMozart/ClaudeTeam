#!/usr/bin/env python3
"""Safe slash-command smoke tests via feishu_router.handle_event.

Default cases are read from runtime_config.json/team.json so cloned teams and
slash-smoke containers do not reuse server-manager IDs or agent names.
"""
import argparse
import json
import os
import sys
import time
import uuid
from pathlib import Path

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import feishu_router

PROJECT_ROOT = Path(__file__).resolve().parent.parent
DEFAULT_BOSS_OPEN_ID = "ou_slash_smoke_test"


BASE_SAFE_CASES = [
    "/help",
    "/team",
    "/usage",
    "/health",
    "/tmux",
    "/send",
    "/stop",
]


def _load_json(path: Path) -> dict:
    try:
        return json.loads(path.read_text())
    except Exception:
        return {}


def _runtime_chat_id() -> str:
    return _load_json(PROJECT_ROOT / "scripts" / "runtime_config.json").get("chat_id", "")


def _worker_agents() -> list[str]:
    agents = _load_json(PROJECT_ROOT / "team.json").get("agents", {})
    return [name for name in agents if name != "manager"]


def build_cases(workers: list[str], include_worker_matrix: bool = True) -> list[str]:
    cases = list(BASE_SAFE_CASES)
    if include_worker_matrix:
        cases.extend(f"/tmux {worker} 5" for worker in workers)
    return cases


def fake_event(text: str, chat_id: str, sender_id: str) -> dict:
    return {
        "message_id": f"smoke_{uuid.uuid4().hex[:12]}",
        "chat_id": chat_id,
        "sender_id": sender_id,
        "text": text,
        "message_type": "text",
    }


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--chat-id", default=os.environ.get("SMOKE_CHAT_ID") or _runtime_chat_id())
    ap.add_argument("--boss-open-id", default=os.environ.get("SMOKE_BOSS_OPEN_ID", DEFAULT_BOSS_OPEN_ID))
    ap.add_argument("--workers", default=",".join(_worker_agents()),
                    help="Comma-separated worker agents for /tmux matrix")
    ap.add_argument("--no-worker-matrix", action="store_true")
    ap.add_argument("--sleep", type=float, default=2.0)
    ap.add_argument("--dry-run", action="store_true",
                    help="Print resolved cases without calling handle_event")
    args = ap.parse_args()

    if not args.chat_id:
        raise SystemExit("❌ 缺少 chat_id：设置 runtime_config.json 或 --chat-id/SMOKE_CHAT_ID")

    workers = [w.strip() for w in args.workers.split(",") if w.strip()]
    cases = build_cases(workers, include_worker_matrix=not args.no_worker_matrix)

    print(f"🧪 冒烟测试开始 — {len(cases)} 条 safe 命令")
    print(f"chat_id={args.chat_id} boss_open_id={args.boss_open_id} workers={workers}\n")
    if args.dry_run:
        for i, cmd in enumerate(cases, 1):
            print(f"[{i}/{len(cases)}] {cmd}")
        print("\n✅ dry-run 结束")
        return
    for i, cmd in enumerate(cases, 1):
        print(f"[{i}/{len(cases)}] 注入: {cmd!r}")
        ev = fake_event(cmd, args.chat_id, args.boss_open_id)
        try:
            feishu_router.handle_event(ev)
            print("         ✅ handle_event 返回")
        except Exception as e:
            print(f"         ❌ 异常: {e}")
        time.sleep(args.sleep)
    print("\n✅ 冒烟测试结束")


if __name__ == "__main__":
    main()
