#!/bin/bash
# ClaudeTeam Docker 入口脚本
# 职责：检查配置 → 启动团队 → 保持容器前台运行
set -e

ROOT="$(cd "$(dirname "$0")/.." && pwd)"
cd "$ROOT"

echo "🐳 ClaudeTeam Docker 启动..."
echo "   Node.js: $(node --version)"
echo "   Python:  $(python3 --version)"
echo "   Claude:  $(claude --version 2>/dev/null || echo 'not found')"
echo "   lark-cli: $(npx @larksuite/cli --version 2>/dev/null || echo 'not found')"
echo ""

# ── 前置检查 ──────────────────────────────────────────────────

if [ ! -f team.json ]; then
  echo "❌ team.json 未找到。请通过 volume 挂载或先运行初始化。"
  echo "   示例：docker run -v ./team.json:/app/team.json ..."
  exit 1
fi

if [ ! -f scripts/runtime_config.json ]; then
  echo "⚠️  runtime_config.json 未找到，尝试运行初始化..."
  python3 scripts/setup.py
fi

# 检查 Claude Code API key
if [ -z "$ANTHROPIC_API_KEY" ]; then
  echo "❌ 环境变量 ANTHROPIC_API_KEY 未设置。"
  echo "   请在 docker-compose.yml 或 docker run -e 中设置。"
  exit 1
fi

# ── 启动团队（非交互模式）────────────────────────────────────

# start-team.sh 最后会 tmux attach，容器内不需要 attach
# 改为启动后保持前台运行

SESSION=$(python3 -c "import json; print(json.load(open('team.json'))['session'])")
AGENTS=($(python3 -c "import json; print(' '.join(json.load(open('team.json'))['agents'].keys()))"))

# 如果 session 已存在（容器重启场景），先清理
if tmux has-session -t "$SESSION" 2>/dev/null; then
  echo "⚠️  清理旧 session: $SESSION"
  tmux kill-session -t "$SESSION"
fi

echo "🚀 启动 Agent 团队..."
echo "   tmux session: $SESSION"
echo "   Agents: ${AGENTS[*]}"

# 创建 tmux session + agent 窗口
tmux new-session -d -s "$SESSION" -n "${AGENTS[0]}" -c "$ROOT"
tmux send-keys -t "$SESSION:${AGENTS[0]}" "claude --dangerously-skip-permissions --name ${AGENTS[0]}" Enter
sleep 2

for agent in "${AGENTS[@]:1}"; do
  tmux new-window -t "$SESSION" -n "$agent" -c "$ROOT"
  tmux send-keys -t "$SESSION:$agent" "claude --dangerously-skip-permissions --name $agent" Enter
  sleep 2
done

# Router（lark-cli WebSocket 事件流）
tmux new-window -t "$SESSION" -n "router" -c "$ROOT"
tmux send-keys -t "$SESSION:router" "npx @larksuite/cli event +subscribe --event-types im.message.receive_v1 --compact --quiet --force | python3 scripts/feishu_router.py --stdin" Enter

# 看板同步
tmux new-window -t "$SESSION" -n "kanban" -c "$ROOT"
tmux send-keys -t "$SESSION:kanban" "python3 scripts/kanban_sync.py daemon" Enter

# Watchdog
tmux new-window -t "$SESSION" -n "watchdog" -c "$ROOT"
tmux send-keys -t "$SESSION:watchdog" "python3 scripts/watchdog.py" Enter

sleep 2

# 发送初始化消息
for agent in "${AGENTS[@]}"; do
  INIT_MSG="你是团队的 ${agent}。

【必读】请读取：agents/${agent}/identity.md — 了解你的角色和通讯规范
【然后立即执行】
1. python3 scripts/feishu_msg.py inbox ${agent}
2. python3 scripts/feishu_msg.py status ${agent} 进行中 \"初始化完成，待命中\"

准备好后，简短汇报：你是谁、当前状态、有无未读消息。"

  tmux send-keys -t "$SESSION:$agent" "$INIT_MSG" Enter
  sleep 1
done

echo ""
echo "✅ 团队已在容器内启动！"
echo "   进入 tmux: docker exec -it <container> tmux attach -t $SESSION"
echo ""

# ── 保持容器前台运行 ──────────────────────────────────────────
# 监听 tmux session，session 结束则容器退出
while tmux has-session -t "$SESSION" 2>/dev/null; do
  sleep 30
done

echo "⚠️  tmux session 已结束，容器退出。"
