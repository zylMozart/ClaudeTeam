'''
A statusline script for Claude. It reads JSON data from stdin and outputs a formatted statusline string.
The statusline includes:
- The model name (e.g., "Claude 2")
- The current working directory (or workspace directory)
- The current git branch (if in a git repository)
- A progress bar showing the percentage of the context window used
- The number of tokens used and the total context window size
The script uses ANSI escape codes for coloring the output. The progress bar changes color based on usage:
- Green for <70% usage
- Yellow for 70-89% usage
- Red for 90%+ usage
To use this script, save it to a file (e.g., `claude_statusline.py`), make it executable, and configure Claude to use it as the statusline script.
'''

from __future__ import annotations

STATUSLINE_SCRIPT = '''#!/usr/bin/env python3
import json
import os
import subprocess
import sys

def fmt_tokens(tokens: int) -> str:
  if tokens >= 1000000:
    return f"{tokens / 1000000:.2f}M"
  elif tokens >= 1000:
    return f"{tokens / 1000:.2f}K"
  else:
    return str(tokens)

def git_branch(cwd: str) -> str:
  try:
    result = subprocess.check_output(
      ['git', '-C', cwd, 'branch', '--show-current'],
      stderr=subprocess.DEVNULL,
      text=True,
      timeout=1.5
    )
    return result.strip()
  except Exception:
    return ''

def main() -> None:
  data = json.load(sys.stdin)
  model = data.get("model", {}).get("display_name", "?")
  cwd = data.get("workspace", {}).get("current_dir") or data.get("cwd", "")
  dirname = os.path.basename(cwd.rstrip("/")) or cwd or "?"

  ctx = data.get("context_window") or {}
  size = ctx.get("context_window_size") or 200_000
  used_input = ctx.get("total_input_tokens") or 0
  used_pct = ctx.get("used_percentage")
  if used_pct is None:
    used_pct = (used_input / size * 100) if size else 0
  pct = int(used_pct)
  branch = git_branch(cwd) if cwd else ""

  CYAN = "\033[36m"
  GREEN = "\033[32m"
  YELLOW = "\033[33m"
  RED = "\033[31m"
  DIM = "\033[2m"
  RESET = "\033[0m"

  if pct >= 90:
    bar_color = RED
  elif pct >= 70:
    bar_color = YELLOW
  else:
    bar_color = GREEN
  width = 10
  filled = min(width, max(0, pct * width // 100))
  bar = "█" * filled + "░" * (width - filled)
  parts = [f"{CYAN}[{model}]{RESET}", f"\U0001f4c1 {dirname}"]
  if branch:
    parts.append(f"\U0001f33f {branch}")
  parts.append(f"{bar_color}{bar}{RESET}")
  parts.append(f"{DIM}{fmt_tokens(used_input)}/{fmt_tokens(size)}{RESET}")
  parts.append(f"({pct}%)")
  print(" | ".join(parts))

if __name__ == "__main__":
  main()
'''

