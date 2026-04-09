#!/bin/bash
# 启动完整 Agent 团队 + Router + Watchdog
# 用法：cd <项目目录> && bash scripts/start-team.sh [--attach]

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
if tmux has-session -t $SESSION 2>/dev/null; then
  echo "⚠️  Session '$SESSION' 已存在"
  echo "   终止旧的: tmux kill-session -t $SESSION"
  echo "   查看: tmux attach -t $SESSION"
  exit 1
fi

echo "🚀 启动 Agent 团队..."
echo "   tmux session: $SESSION"
echo "   Agents: ${AGENTS[*]}"
echo ""

# ── 确保 CLAUDE.md 存在（由 setup.py 自动生成）──────────────────
if [ ! -f CLAUDE.md ]; then
  echo "⚠️  CLAUDE.md 不存在，请先运行: python3 scripts/setup.py"
fi

# ── Pre-trust：预注册项目目录，跳过后续 trust dialog ──────────
echo "🔐 预注册项目目录..."
claude -p "echo ok" --dangerously-skip-permissions > /dev/null 2>&1 || true
echo "   ✅ 完成"
echo ""

# ── 创建 tmux session ─────────────────────────────────────────

# window 0: 第一个 agent
tmux new-session -d -s $SESSION -n "${AGENTS[0]}" -c "$ROOT"
tmux send-keys -t $SESSION:${AGENTS[0]} "claude --dangerously-skip-permissions" Enter

# 其他 Agent 窗口
for agent in "${AGENTS[@]:1}"; do
  tmux new-window -t $SESSION -n "$agent" -c "$ROOT"
  tmux send-keys -t $SESSION:$agent "claude --dangerously-skip-permissions" Enter
  sleep 1
done

# 守护进程窗口
tmux new-window -t $SESSION -n "router" -c "$ROOT"
tmux send-keys -t $SESSION:router "python3 scripts/feishu_router.py" Enter

tmux new-window -t $SESSION -n "kanban" -c "$ROOT"
tmux send-keys -t $SESSION:kanban "python3 scripts/kanban_sync.py daemon" Enter

tmux new-window -t $SESSION -n "watchdog" -c "$ROOT"
tmux send-keys -t $SESSION:watchdog "python3 scripts/watchdog.py" Enter

# ── 智能发送初始化消息（等待 ❯ 提示符就绪）─────────────────────
echo ""
echo "⏳ 等待 Agent 就绪并发送初始化消息..."

AGENTS_STR="${AGENTS[*]}"
python3 -c "
import sys, time
sys.path.insert(0, 'scripts')
from tmux_utils import inject_when_idle, wait_for_ready, auto_accept_trust

SESSION = '$SESSION'
AGENTS = '$AGENTS_STR'.split()

for agent in AGENTS:
    print(f'  ⏳ {agent}...', end=' ', flush=True)

    # 方案 C 兜底：先检测是否卡在 trust dialog
    if auto_accept_trust(SESSION, agent, timeout=5):
        print('(trust dialog 已自动确认)', end=' ', flush=True)

    # 方案 B：正向检测 ❯ 提示符
    if wait_for_ready(SESSION, agent, timeout=30):
        print('✅ 就绪', flush=True)
    else:
        print('⚠️ 超时，强制发送', flush=True)

    msg = f'''你是团队的 {agent}。

【必读】请读取：agents/{agent}/identity.md — 了解你的角色和通讯规范
【然后立即执行】
1. python3 scripts/feishu_msg.py inbox {agent}
2. python3 scripts/feishu_msg.py status {agent} 进行中 \"初始化完成，待命中\"

准备好后，简短汇报：你是谁、当前状态、有无未读消息。'''

    inject_when_idle(SESSION, agent, msg, wait_secs=5)
    time.sleep(0.5)
"

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
echo "  查看团队: tmux attach -t $SESSION"
echo "  切换窗口: Ctrl+B, n/p 或 Ctrl+B, 0-${#AGENTS[@]}"
echo ""
echo "  飞书测试:"
echo "    python3 scripts/feishu_msg.py send ${AGENTS[1]:-writer} ${AGENTS[0]} \"请处理一个任务\" 高"
echo "    python3 scripts/feishu_msg.py inbox ${AGENTS[0]}"

# 切到第一个 agent 窗口
tmux select-window -t $SESSION:${AGENTS[0]}

# --attach 参数时才自动 attach（默认不阻塞，方便自动化）
if [[ "${1:-}" == "--attach" ]]; then
  tmux attach -t $SESSION
else
  echo "  运行 tmux attach -t $SESSION 进入团队"
fi
