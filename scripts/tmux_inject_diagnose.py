#!/usr/bin/env python3
"""Scan tmux panes for unsubmitted input residue."""
import argparse
import json
import os
import subprocess
import sys

sys.path.insert(0, os.path.dirname(__file__))
from config import AGENTS, TMUX_SESSION
from tmux_utils import capture_pane, detect_unsubmitted_input_text


def _run(cmd):
    return subprocess.run(cmd, capture_output=True, text=True, timeout=5)


def list_agent_windows(session):
    names = list(AGENTS.keys())
    if names:
        return names
    r = _run(["tmux", "list-windows", "-t", session, "-F", "#{window_name}"])
    if r.returncode != 0:
        return []
    return [line.strip() for line in r.stdout.splitlines() if line.strip()]


def scan(session=TMUX_SESSION, agents=None):
    rows = []
    for agent in agents or list_agent_windows(session):
        pane = capture_pane(session, agent)
        if not pane:
            continue
        residual = detect_unsubmitted_input_text(pane)
        if not residual:
            continue
        tail = " ".join("\n".join(pane.splitlines()[-8:]).split())[-240:]
        rows.append({
            "agent": agent,
            "pane": f"{session}:{agent}",
            "residual": residual[-240:],
            "tail": tail,
        })
    return rows


def main(argv=None):
    parser = argparse.ArgumentParser(description="Detect unsubmitted tmux input residue")
    parser.add_argument("--session", default=TMUX_SESSION)
    parser.add_argument("--agent", action="append", default=[])
    parser.add_argument("--json", action="store_true")
    args = parser.parse_args(argv)

    rows = scan(args.session, args.agent or None)
    if args.json:
        print(json.dumps(rows, ensure_ascii=False, indent=2))
        return 1 if rows else 0
    if not rows:
        print("✅ 未发现未提交输入残留")
        return 0
    print(f"⚠️ 发现 {len(rows)} 个 pane 存在疑似未提交输入残留:")
    for row in rows:
        print(f"── {row['agent']} ({row['pane']})")
        print(f"   residual: {row['residual']}")
        print(f"   tail: {row['tail']}")
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
