#!/bin/bash
# 启动完整 Agent 团队 + Router + Watchdog
# 用法：cd <项目目录> && bash scripts/start-team.sh

set -e
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
cd "$ROOT"

# 前置检查
if ! command -v tmux &>/dev/null; then
  echo "❌ 未安装 tmux，请先安装: brew install tmux (macOS) 或 apt install tmux (Linux)"
  exit 1
fi

if [ ! -f team.json ]; then
  echo "❌ team.json 未找到。请先用 Claude Code 打开本项目完成初始化，"
  echo "   或手动创建 team.json（参见 README.md）"
  exit 1
fi

if [ ! -f scripts/runtime_config.json ]; then
  echo "❌ 尚未初始化飞书资源。请先运行: python3 scripts/setup.py"
  exit 1
fi

SESSION=$(python3 -c "import json; print(json.load(open('team.json'))['session'])")
AGENTS=($(python3 -c "import json; print(' '.join(json.load(open('team.json'))['agents'].keys()))"))

# 检查 session 是否已存在
if tmux has-session -t "$SESSION" 2>/dev/null; then
  echo "⚠️  Session '$SESSION' 已存在"
  echo "   终止旧的: tmux kill-session -t $SESSION"
  echo "   查看: tmux attach -t $SESSION"
  exit 1
fi

echo "🚀 启动 Agent 团队..."
echo "   tmux session: ${SESSION}"
echo "   Agents: ${AGENTS[*]}"
echo ""

# ── 创建 tmux session ─────────────────────────────────────────

# window 0: 第一个 agent
tmux new-session -d -s "$SESSION" -n "${AGENTS[0]}" -c "$ROOT"
tmux send-keys -t "$SESSION:${AGENTS[0]}" "claude --dangerously-skip-permissions --name ${AGENTS[0]}" Enter
sleep 2

# 其他 Agent 窗口
for agent in "${AGENTS[@]:1}"; do
  tmux new-window -t "$SESSION" -n "$agent" -c "$ROOT"
  tmux send-keys -t "$SESSION:$agent" "claude --dangerously-skip-permissions --name $agent" Enter
  sleep 2
done

# window: router (lark-cli event stream → router)
# 从 runtime_config.json 读取 lark_profile，确保多项目隔离
LARK_PROFILE=$(python3 -c "import json; print(json.load(open('scripts/runtime_config.json')).get('lark_profile',''))" 2>/dev/null)
PROFILE_FLAG=""
if [ -n "$LARK_PROFILE" ]; then
  PROFILE_FLAG="--profile $LARK_PROFILE"
fi
tmux new-window -t "$SESSION" -n "router" -c "$ROOT"
tmux send-keys -t "$SESSION:router" "npx @larksuite/cli $PROFILE_FLAG event +subscribe --event-types im.message.receive_v1 --compact --quiet --force | python3 scripts/feishu_router.py --stdin" Enter

# window: kanban (看板同步守护进程)
tmux new-window -t "$SESSION" -n "kanban" -c "$ROOT"
tmux send-keys -t "$SESSION:kanban" "python3 scripts/kanban_sync.py daemon" Enter

# window: watchdog
tmux new-window -t "$SESSION" -n "watchdog" -c "$ROOT"
tmux send-keys -t "$SESSION:watchdog" "python3 scripts/watchdog.py" Enter

sleep 2

# ── 发送初始化消息给每个 Agent ───────────────────────────────

for agent in "${AGENTS[@]}"; do
  INIT_MSG="你是团队的 ${agent}。

【必读】请读取：agents/${agent}/identity.md — 了解你的角色和通讯规范
【然后立即执行】
1. python3 scripts/feishu_msg.py inbox ${agent}    # 查看收件箱
2. python3 scripts/feishu_msg.py status ${agent} 进行中 \"初始化完成，待命中\"

准备好后，简短汇报：你是谁、当前状态、有无未读消息。"

  tmux send-keys -t "$SESSION:$agent" "$INIT_MSG" Enter
  sleep 1
done

echo ""
echo "✅ 团队已启动！"
echo ""
echo "  tmux 窗口:"
for agent in "${AGENTS[@]}"; do
  echo "    $agent    — Claude agent"
done
echo "    router    — 消息路由守护进程"
echo "    kanban    — 看板同步守护进程（60秒一次）"
echo "    watchdog  — 看门狗（监控 Router + 看板同步）"
echo ""
echo "  查看团队: tmux attach -t ${SESSION}"
echo "  切换窗口: Ctrl+B, n/p 或 Ctrl+B, 0-${#AGENTS[@]}"
echo ""
echo "  飞书测试:"
echo "    python3 scripts/feishu_msg.py send ${AGENTS[1]:-writer} ${AGENTS[0]} \"请处理一个任务\" 高"
echo "    python3 scripts/feishu_msg.py inbox ${AGENTS[0]}"

# 切到第一个 agent 窗口
tmux select-window -t "$SESSION:${AGENTS[0]}"
tmux attach -t "$SESSION"
