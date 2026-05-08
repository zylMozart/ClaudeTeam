---
name: tmux-boundary-diagnose
description: "Diagnose tmux injection, unsafe residual input, lazy wake, and shell pollution boundaries."
---

# Tmux Boundary Diagnose

Use this skill when an agent has an inbox task but did not act, a pane shows
unsubmitted input, or startup/wake text appears to have been sent into a shell.

## Boundary

- Default is read-only capture and process inspection.
- Do not inject text, press Enter, clear input, or kill processes without explicit approval.
- Runtime fixes belong in `tmux_utils`, router delivery, lifecycle, or queue code.

## Procedure

1. Capture the target pane tail.
2. Check for shell pollution such as natural-language lines followed by `command not found`.
3. Check for residual unsubmitted input.
4. Check whether the CLI process exists below the tmux pane process.
5. Compare delivery paths:
   - router path: wake, ready wait, `inject_when_idle`, pending queue
   - `feishu_msg.py send/direct`: inbox write plus best-effort tmux notification
6. Report whether the issue is Bitable write, tmux notification, lifecycle wake, or agent action.

## Read-Only Commands

```bash
tmux capture-pane -t <session>:<agent> -p -S -120
tmux display-message -t <session>:<agent> -p "#{pane_pid}:#{pane_current_command}:#{pane_current_path}"
python3 scripts/feishu_msg.py inbox <agent>
```

## Report

```bash
python3 scripts/feishu_msg.py send manager <agent> "<root cause + evidence + safe next step>" 高
```
