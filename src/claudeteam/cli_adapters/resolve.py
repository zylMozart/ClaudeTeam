#!/usr/bin/env python3
"""Shell bridge — 供 bash 脚本消费 adapter 属性。

用法:
  python3 scripts/cli_adapters/resolve.py <agent> spawn_cmd <model>
  python3 scripts/cli_adapters/resolve.py <agent> resume_cmd <model> <sid>
  python3 scripts/cli_adapters/resolve.py <agent> ready_markers
  python3 scripts/cli_adapters/resolve.py <agent> busy_markers
  python3 scripts/cli_adapters/resolve.py <agent> process_name
"""
import sys
import os
from pathlib import Path

THIS_FILE = Path(__file__).resolve()
PROJECT_ROOT = THIS_FILE.parents[3]
SRC_ROOT = PROJECT_ROOT / "src"
SCRIPTS_ROOT = PROJECT_ROOT / "scripts"

# 直接脚本执行时确保 claudeteam 包与 legacy scripts/config.py 都可导入。
if str(SRC_ROOT) not in sys.path:
    sys.path.insert(0, str(SRC_ROOT))
if str(SCRIPTS_ROOT) not in sys.path:
    sys.path.insert(0, str(SCRIPTS_ROOT))

from claudeteam.cli_adapters import adapter_for_agent


def main():
    if len(sys.argv) < 3:
        print(f"用法: {sys.argv[0]} <agent> <attr> [args...]",
              file=sys.stderr)
        sys.exit(2)

    agent, attr = sys.argv[1], sys.argv[2]
    adapter = adapter_for_agent(agent)

    if attr == "spawn_cmd":
        model = sys.argv[3] if len(sys.argv) > 3 else ""
        print(adapter.spawn_cmd(agent, model))
    elif attr == "resume_cmd":
        model = sys.argv[3] if len(sys.argv) > 3 else ""
        sid = sys.argv[4] if len(sys.argv) > 4 else ""
        result = adapter.resume_cmd(agent, model, sid)
        if result is None:
            sys.exit(1)
        print(result)
    elif attr == "ready_markers":
        print(r"\|".join(adapter.ready_markers()))
    elif attr == "busy_markers":
        print(r"\|".join(adapter.busy_markers()))
    elif attr == "process_name":
        print(adapter.process_name())
    elif attr == "thinking_init_hint":
        thinking = sys.argv[3] if len(sys.argv) > 3 else "default"
        hint = adapter.thinking_init_hint(thinking)
        if hint is None:
            sys.exit(1)
        print(hint)
    else:
        print(f"Unknown attribute: {attr}", file=sys.stderr)
        sys.exit(2)


if __name__ == "__main__":
    main()
