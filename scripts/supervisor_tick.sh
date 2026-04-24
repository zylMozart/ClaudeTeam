#!/usr/bin/env bash
# scripts/supervisor_tick.sh — supervisor (监工) cron 入口 (lazy_wake_v2 §A.3)
#
# 由宿主 cron / docker-compose / systemd timer 驱动,每 15 分钟跑一次。
# 自身不做循环 — supervisor 是非常驻角色,跑完一轮就回到休眠。
#
# 三种情况:
#   1) 窗口存在 + Claude 活着  → inject_when_idle 喂"巡查一轮"
#   2) 窗口存在 + Claude 挂了  → lifecycle wake (resume saved session)
#   3) 窗口不存在               → lifecycle spawn (冷启动新 session)
#
# 步长 / idle 阈值统一从 CLAUDETEAM_SUSPEND_IDLE_MIN 读 (默认 15,绑定不变式)。

set -e
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
cd "$ROOT"

AGENT="supervisor"
SESSION=$(python3 -c "import json; print(json.load(open('team.json'))['session'])" 2>/dev/null || echo "ClaudeTeam")
IDLE_MIN="${CLAUDETEAM_SUSPEND_IDLE_MIN:-15}"
TICK_PROMPT="巡查一轮: 读 agents/supervisor/workspace/overrides.json,扫所有非白名单 agent 的 inbox+pane+状态表,按 NL 规则输出 SUSPEND/KEEP 决策并落盘到 decisions/$(date +%Y-%m-%d).jsonl,然后自休眠。"

source "$ROOT/scripts/lib/agent_lifecycle.sh"

if tmux has-session -t "$SESSION:$AGENT" 2>/dev/null; then
  # 复用 lifecycle 的父子进程查找,避免重复实现
  if [[ -n "$(_lifecycle_pids_for_agent "$AGENT")" ]]; then
    # 路径 1: 窗口在 + 进程活 → 直接喂指令
    python3 - <<PY
import sys; sys.path.insert(0, "$ROOT/src")
from claudeteam.runtime.tmux_utils import inject_when_idle
inject_when_idle("$SESSION", "$AGENT", """$TICK_PROMPT""", wait_secs=10, force_after_wait=False)
PY
    echo "🔔 supervisor_tick: 已喂 tick 指令 (idle_min=$IDLE_MIN)"
  else
    # 路径 2: 窗口在但 Claude 挂了 → wake (resume)
    wake_agent "$AGENT"
    sleep 3
    python3 - <<PY
import sys; sys.path.insert(0, "$ROOT/src")
from claudeteam.runtime.tmux_utils import inject_when_idle
inject_when_idle("$SESSION", "$AGENT", """$TICK_PROMPT""", wait_secs=20, force_after_wait=False)
PY
    echo "🌅 supervisor_tick: wake + 喂 tick (idle_min=$IDLE_MIN)"
  fi
else
  # 路径 3: 窗口不存在 → 冷启动
  spawn_agent "$AGENT"
  sleep 5
  python3 - <<PY
import sys; sys.path.insert(0, "$ROOT/src")
from claudeteam.runtime.tmux_utils import inject_when_idle
inject_when_idle("$SESSION", "$AGENT", """$TICK_PROMPT""", wait_secs=30, force_after_wait=False)
PY
  echo "🟢 supervisor_tick: spawn + 喂 tick (idle_min=$IDLE_MIN)"
fi
