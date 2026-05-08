#!/usr/bin/env bash
# scripts/supervisor_tick.sh — supervisor (监工) 周期性触发入口 (方案 §2.2.2)
#
# 每次调用:
#   - 读 /app/state/supervisor_cursor.txt → 拿本轮目标 agent
#   - 确保 supervisor 窗口/进程活着 (三路径: inject / wake+inject / spawn+inject)
#   - 喂精简 prompt: "处理一个 agent, 产出一行决策"
#   - 把 cursor 推进到下一个 non-whitelist agent
#
# 关键约束 (决策/执行解耦):
#   - supervisor 只写决策,绝不自己调 suspend_agent
#   - 真执行留给 scripts/supervisor_apply.sh (ticker 循环每轮 tick 后追跑一次)

set -e
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
cd "$ROOT"

AGENT="supervisor"
SESSION="$(python3 -c "import json; print(json.load(open('team.json'))['session'])" 2>/dev/null || echo "ClaudeTeam")"
IDLE_MIN="${CLAUDETEAM_SUSPEND_IDLE_MIN:-15}"
STATE_DIR="${CLAUDETEAM_STATE_DIR:-/app/state}"
CURSOR_FILE="$STATE_DIR/supervisor_cursor.txt"
WS="$ROOT/agents/supervisor/workspace"
OVERRIDES="$WS/overrides.json"
DECISIONS="$WS/decisions/$(date +%F).jsonl"

mkdir -p "$STATE_DIR" "$WS/decisions"

# 计算本轮目标 agent: 轮询 team.json 里的 agents 减去 overrides.never_suspend
TARGET="$(python3 - <<PY
import json, os, pathlib
team = json.load(open("team.json"))
agents = [a for a in team.get("agents", {}).keys()]
wl = set()
try:
    wl = set(json.load(open("$OVERRIDES")).get("never_suspend", []))
except Exception:
    pass
candidates = [a for a in agents if a not in wl]
if not candidates:
    print("")
else:
    cursor_file = "$CURSOR_FILE"
    cur = ""
    try:
        cur = open(cursor_file).read().strip()
    except Exception:
        pass
    if cur in candidates:
        idx = (candidates.index(cur) + 1) % len(candidates)
    else:
        idx = 0
    nxt = candidates[idx]
    open(cursor_file, "w").write(nxt + "\n")
    print(nxt)
PY
)"

if [[ -z "$TARGET" ]]; then
  echo "⚠️ supervisor_tick: no non-whitelist agent to target (all filtered); skip"
  exit 0
fi

echo "🎯 supervisor_tick: target=$TARGET idle_min=$IDLE_MIN"

# 精简 prompt — 只处理一个 agent,一行决策
TICK_PROMPT="你是 supervisor,本轮只处理 1 个 agent: ${TARGET}。流程:
1. 读 ${OVERRIDES} 的 idle_min_override (该 agent 自定阈值; 缺省=${IDLE_MIN} 分钟)
2. 查该 agent 的 pane+inbox+状态:
   - tmux capture-pane -t ${SESSION}:${TARGET} -p | tail -50
   - python3 scripts/feishu_msg.py inbox ${TARGET} 2>/dev/null | tail -10
   - python3 scripts/feishu_msg.py status ${TARGET} 2>/dev/null | tail -5
3. 判定: idle 分钟数 >= 阈值 且 inbox 为空 且 pane 无活动 → action=SUSPEND; 否则 action=KEEP
4. 追加一行 JSON 到 ${DECISIONS}:
   {\"ts\": <unix_ts>, \"agent\": \"${TARGET}\", \"action\": \"SUSPEND|KEEP\", \"reason\": \"<一句话>\", \"idle_min\": <int>}
5. 回复一句 \"done ${TARGET}: <action>\"
**严禁自己调 suspend_agent / scripts/lib/agent_lifecycle.sh**; 执行留给 supervisor_apply.sh。"

source "$ROOT/scripts/lib/agent_lifecycle.sh"

inject_prompt() {
  local wait_secs="$1"
  python3 - <<PY
import sys; sys.path.insert(0, "$ROOT/src")
from claudeteam.runtime.tmux_utils import inject_when_idle
inject_when_idle("$SESSION", "$AGENT", """$TICK_PROMPT""", wait_secs=$wait_secs, force_after_wait=False)
PY
}

if tmux has-session -t "$SESSION:$AGENT" 2>/dev/null; then
  if [[ -n "$(_lifecycle_pids_for_agent "$AGENT")" ]]; then
    inject_prompt 10
    echo "🔔 supervisor_tick: inject → $TARGET (idle_min=$IDLE_MIN)"
  else
    wake_agent "$AGENT"
    sleep 3
    inject_prompt 20
    echo "🌅 supervisor_tick: wake + inject → $TARGET"
  fi
else
  spawn_agent "$AGENT"
  sleep 5
  inject_prompt 30
  echo "🟢 supervisor_tick: spawn + inject → $TARGET"
fi
