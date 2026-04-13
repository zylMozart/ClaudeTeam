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

# 清理上一轮残留的 router 孤儿进程。
# tmux kill-session 只杀 shell 前台进程,管道里的 npx/node/python 会被 reparent 到 init,
# 新 session 启动时会和这些老订阅一起抢事件,导致部分消息丢失(Bug 13)。
ORPHAN_COUNT=$(pgrep -f "event +subscribe --event-types im.message.receive_v1" | wc -l)
if [ "$ORPHAN_COUNT" -gt 0 ]; then
  echo "🧹 清理 $ORPHAN_COUNT 个 router 孤儿进程..."
  pkill -f "event +subscribe --event-types im.message.receive_v1" 2>/dev/null || true
  pkill -f "feishu_router.py" 2>/dev/null || true
  sleep 1
fi

# ── npx warm-up (P1-12) ────────────────────────────────────────
# 首次运行 npx @larksuite/cli 会从 npm registry 拉取 ~80MB 的包, 期间 stdout
# 基本静默, 慢网下用户会误以为脚本卡死。如果不在这里 warm-up, 下载会发生在
# 后面 router 窗口的 `npx event +subscribe` 里 —— 那在 tmux pane 内, 终端用户
# 完全看不到, 更糟糕: probe 的 15 秒轮询会在 router 还没下载完时误判失败。
# 所以先在宿主 shell 里同步跑一次 `--version`, 让用户对这段等待有预期。
echo "📦 预热 lark-cli (npx 首次使用会下载约 80MB, 慢网可能需 1-2 分钟)..."
if npx --yes @larksuite/cli --version >/dev/null 2>&1; then
  echo "   ✓ lark-cli ready"
else
  echo "   ⚠️ warm-up 失败, router 启动阶段可能仍会尝试下载 (请 tmux attach -t router 观察)"
fi

echo "🚀 启动 Agent 团队..."
echo "   tmux session: ${SESSION}"
echo "   Agents: ${AGENTS[*]}"
echo ""

# ── 创建 tmux session ─────────────────────────────────────────

# window 0: 第一个 agent
tmux new-session -d -s "$SESSION" -n "${AGENTS[0]}" -c "$ROOT"
tmux send-keys -t "$SESSION:${AGENTS[0]}" "IS_SANDBOX=1 claude --dangerously-skip-permissions --name ${AGENTS[0]}" Enter
sleep 2

# 其他 Agent 窗口
for agent in "${AGENTS[@]:1}"; do
  tmux new-window -t "$SESSION" -n "$agent" -c "$ROOT"
  tmux send-keys -t "$SESSION:$agent" "IS_SANDBOX=1 claude --dangerously-skip-permissions --name $agent" Enter
  sleep 2
done

# window: router (lark-cli event stream → router)
# 从 runtime_config.json 读取 lark_profile，确保多项目隔离
LARK_PROFILE=$(python3 -c "import json; print(json.load(open('scripts/runtime_config.json')).get('lark_profile') or '')" 2>/dev/null)
PROFILE_FLAG=""
if [ -n "$LARK_PROFILE" ]; then
  PROFILE_FLAG="--profile $LARK_PROFILE"
fi
tmux new-window -t "$SESSION" -n "router" -c "$ROOT"
tmux send-keys -t "$SESSION:router" "npx @larksuite/cli $PROFILE_FLAG event +subscribe --event-types im.message.receive_v1 --compact --quiet --force --as bot | python3 scripts/feishu_router.py --stdin" Enter

# window: kanban (看板同步守护进程)
tmux new-window -t "$SESSION" -n "kanban" -c "$ROOT"
tmux send-keys -t "$SESSION:kanban" "python3 scripts/kanban_sync.py daemon" Enter

# window: watchdog
tmux new-window -t "$SESSION" -n "watchdog" -c "$ROOT"
tmux send-keys -t "$SESSION:watchdog" "python3 scripts/watchdog.py" Enter

# ── 验证每个 Agent 窗口里 Claude 真的起来了 (Bug 11 防御) ─────
# 如果窗口里只剩 bash,后续 init 消息会被当成 shell 命令跑,看起来"启动了"
# 实际全员死亡。所以先 probe 每个窗口,没进 Claude UI 的直接 abort。
#
# 探测/诊断逻辑抽在 scripts/lib/tmux_team_bringup.sh,和 docker-entrypoint.sh 共享。
# 宿主机入口失败时直接 exit 1(退出决策留给调用方,不在库里调 exit)。
source "$ROOT/scripts/lib/tmux_team_bringup.sh"

if ! probe_claude_agents 15; then
  diagnose_failed_agents
  echo ""
  echo "⚠️  中止:不向死掉的 agent 窗口发送 init 消息,以免污染 bash 历史。"
  echo "   修好启动问题后,tmux kill-session -t $SESSION && bash scripts/start-team.sh"
  exit 1
fi

# ── 发送初始化消息给每个 Agent ───────────────────────────────

for agent in "${AGENTS[@]}"; do
  INIT_MSG="你是团队的 ${agent}。

【必读】请读取：agents/${agent}/identity.md — 了解你的角色和通讯规范
【然后立即执行】
1. python3 scripts/feishu_msg.py inbox ${agent}    # 查看收件箱
2. python3 scripts/feishu_msg.py status ${agent} 进行中 \"初始化完成，待命中\"

准备好后，简短汇报：你是谁、当前状态、有无未读消息。"

  tmux send-keys -t "$SESSION:$agent" "$INIT_MSG" Enter
  # 每个 agent init 会同时调用 feishu_msg.py status → Bitable record-batch-create
  # 撞到飞书限流。错峰 2.5s 避免 Bug 15 的并发写入失败。
  sleep 2.5
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

# 只在交互式终端下 attach。非 TTY 环境 (CI/脚本/嵌套 agent) 下 tmux attach
# 会报 "open terminal failed: not a terminal" 并用退出码 1 污染上游调用者的
# 判断。session 本身已经跑起来了,没必要用 attach 阻塞。
if [ -t 1 ] && [ -t 0 ]; then
  tmux attach -t "$SESSION"
else
  echo "ℹ️  非交互式运行,跳过 tmux attach。查看团队: tmux attach -t $SESSION"
fi
