#!/usr/bin/env bash
# 启动完整 Agent 团队 + Router + Watchdog
# 用法：cd <项目目录> && bash scripts/start-team.sh
#
# 依赖: bash 4+ (使用 declare -A 关联数组存 per-agent 模型分配)。
# macOS 自带 bash 3.2,需 `brew install bash` 后确保 PATH 命中 bash 4。
# env bash shebang 让同机器的多 bash 版本按 PATH 顺序决定。

set -e
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
cd "$ROOT"

# ── lazy-mode CLI / env 解析 ─────────────────────────────────
# lazy-mode: 只启动白名单 (manager/supervisor/router/kanban/watchdog),
# 业务 agent (coder/writer/...) 只建 tmux 窗口,不跑 claude —— 由 supervisor
# 监工 + router wake_on_deliver 按需唤醒。设计源: agents/architect/workspace/
# design/lazy_wake_v2.md §A.2/A.8。
#
# 优先级: CLI flag > env CLAUDETEAM_LAZY_MODE > 默认 on。
LAZY_MODE="${CLAUDETEAM_LAZY_MODE:-on}"
while [ "$#" -gt 0 ]; do
  case "$1" in
    --lazy-mode)    LAZY_MODE=on;  shift ;;
    --no-lazy-mode) LAZY_MODE=off; shift ;;
    -h|--help)
      cat <<EOF
用法: bash scripts/start-team.sh [--lazy-mode | --no-lazy-mode]

  --lazy-mode      (默认) 只启动白名单 agent (manager/supervisor) + 基础守护
                   (router/kanban/watchdog)。业务 agent 窗口显示 '💤 待 wake',
                   由 router 在业务消息到达时按需唤醒。
  --no-lazy-mode   启动所有 team.json 里的 agent,行为等同旧版。

  环境变量 CLAUDETEAM_LAZY_MODE=on|off 作为 fallback,
  命令行 flag 优先级高于环境变量。
EOF
      exit 0 ;;
    *)
      echo "❌ 未知参数: $1" >&2
      echo "   用法: bash scripts/start-team.sh [--lazy-mode | --no-lazy-mode]" >&2
      exit 2 ;;
  esac
done
case "$LAZY_MODE" in
  on|off) ;;
  *)
    echo "❌ CLAUDETEAM_LAZY_MODE 取值非法: '$LAZY_MODE' (期望 on 或 off)" >&2
    exit 2 ;;
esac

# 白名单 + lazy 决策从 lib/tmux_team_bringup.sh source 进来 (LAZY_WHITELIST_AGENTS,
# is_lazy_whitelist, should_skip_agent_in_lazy_mode)。docker-entrypoint.sh 同源,
# 保证宿主/容器一致 (lazy_wake_v2 §A.2)。
source "$(cd "$(dirname "$0")" && pwd)/lib/tmux_team_bringup.sh"

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
echo "   lazy-mode: ${LAZY_MODE}"
if [ "$LAZY_MODE" = "on" ]; then
  echo "     ↳ 会跑 claude 的 agent: ${LAZY_WHITELIST_AGENTS[*]}"
  echo "     ↳ 其它业务 agent 窗口创建后显示 '💤 待 wake',由 router 唤醒"
fi
echo ""

# ── 预解析每个 agent 的模型 (lazy_wake_v2 §B) ─────────────────
# resolve_all_agent_models 抽在 tmux_team_bringup.sh,start-team.sh 和
# docker-entrypoint.sh 同源,避免宿主/容器对模型解析出现漂移。
# 库不 exit,失败时由本脚本把 return 1 放大为 exit 1。
if ! resolve_all_agent_models; then
  echo "   中止启动 (失败的 agent: ${FAILED_MODEL_AGENT})"
  exit 1
fi
print_agent_models_table
echo ""

# ── 创建 tmux session ─────────────────────────────────────────

# spawn_agent_window <agent_name> [--first]
#   --first  使用 new-session 创建(每个 session 只能第一次用一次)
#   否则     使用 new-window 追加到既有 session
#
# lazy-mode off 或 agent 在白名单里: 发 claude 启动命令。
# lazy-mode on 且 agent 不在白名单:
#   pane 停在 bash 提示符,先 clear + echo 一个醒目的占位 banner,
#   等 router::wake_on_deliver 将来 send-keys 真正的 claude 启动命令。
spawn_agent_window() {
  local agent="$1" first="${2:-}"
  if [ "$first" = "--first" ]; then
    tmux new-session -d -s "$SESSION" -n "$agent" -c "$ROOT"
  else
    tmux new-window -t "$SESSION" -n "$agent" -c "$ROOT"
  fi

  if should_skip_agent_in_lazy_mode "$agent"; then
    # 占位: 留 bash prompt,不 spawn claude。router 唤醒时会覆盖这条。
    # 两行 echo 足够 —— 目标是让 attach 进来的人立刻看懂这不是"启动失败"。
    local banner="💤 待 wake  (agent=$agent, model=${AGENT_MODELS[$agent]}, lazy-mode)"
    tmux send-keys -t "$SESSION:$agent" \
      "clear && echo '$banner' && echo '   router 收到业务消息后会唤醒本窗口'" \
      Enter
  else
    tmux send-keys -t "$SESSION:$agent" \
      "IS_SANDBOX=1 claude --dangerously-skip-permissions --model ${AGENT_MODELS[$agent]} --name $agent" \
      Enter
  fi
}

spawn_agent_window "${AGENTS[0]}" --first
sleep 2

for agent in "${AGENTS[@]:1}"; do
  spawn_agent_window "$agent"
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
# 探测/诊断逻辑抽在 scripts/lib/tmux_team_bringup.sh,已在文件开头 source。
# 宿主机入口失败时直接 exit 1(退出决策留给调用方,不在库里调 exit)。

# lazy-mode 下只 probe/init 实际跑了 claude 的 agent,占位窗口跳过。
# 这里用 PROBE_AGENTS 覆盖 tmux_team_bringup.sh 默认取的 $AGENTS。
if [ "$LAZY_MODE" = "on" ]; then
  ACTIVE_AGENTS=()
  for agent in "${AGENTS[@]}"; do
    if is_lazy_whitelist "$agent"; then
      ACTIVE_AGENTS+=("$agent")
    fi
  done
  if [ ${#ACTIVE_AGENTS[@]} -eq 0 ]; then
    echo "❌ lazy-mode 下 team.json 里没有任何白名单 agent"
    echo "   白名单: ${LAZY_WHITELIST_AGENTS[*]}"
    echo "   至少要有 manager 才能组建团队 — 或者加 --no-lazy-mode"
    exit 1
  fi
else
  ACTIVE_AGENTS=("${AGENTS[@]}")
fi
export PROBE_AGENTS="${ACTIVE_AGENTS[*]}"

if ! probe_claude_agents 15; then
  diagnose_failed_agents
  echo ""
  echo "⚠️  中止:不向死掉的 agent 窗口发送 init 消息,以免污染 bash 历史。"
  echo "   修好启动问题后,tmux kill-session -t $SESSION && bash scripts/start-team.sh"
  exit 1
fi

# ── 发送初始化消息给每个 Agent ───────────────────────────────
# lazy-mode 下占位窗口里只有 bash,发 init 会被当成 shell 命令跑 → 同 Bug 11,
# 所以只给 ACTIVE_AGENTS 发,占位 agent 等 router 唤醒时自己的 wake 路径再发。

for agent in "${ACTIVE_AGENTS[@]}"; do
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
echo "✅ 团队已启动！(lazy-mode=$LAZY_MODE)"
echo ""
echo "  tmux 窗口:"
for agent in "${AGENTS[@]}"; do
  if should_skip_agent_in_lazy_mode "$agent"; then
    echo "    $agent    — 💤 待 wake (lazy-mode 占位)"
  else
    echo "    $agent    — Claude agent"
  fi
done
echo "    router    — 消息路由守护进程"
echo "    kanban    — 看板同步守护进程（60秒一次）"
echo "    watchdog  — 看门狗（监控 Router + 看板同步）"
echo ""
echo "  查看团队: tmux attach -t ${SESSION}"
echo "  切换窗口: Ctrl+B, n/p 或 Ctrl+B, 0-${#AGENTS[@]}"
if [ "$LAZY_MODE" = "on" ]; then
  echo ""
  echo "  💡 lazy-mode 提示:"
  echo "     - 业务 agent 会在飞书消息到达时由 router::wake_on_deliver 自动唤醒"
  echo "     - 想全量启动: bash scripts/start-team.sh --no-lazy-mode"
fi
echo ""
echo "  飞书测试:"
echo "    python3 scripts/feishu_msg.py send ${AGENTS[1]:-writer} ${AGENTS[0]} \"请处理一个任务\" 高"
echo "    python3 scripts/feishu_msg.py inbox ${AGENTS[0]}"

# 切到第一个 active agent 窗口(优先白名单,避免落在 💤 占位窗口)
if [ ${#ACTIVE_AGENTS[@]} -gt 0 ]; then
  tmux select-window -t "$SESSION:${ACTIVE_AGENTS[0]}"
else
  tmux select-window -t "$SESSION:${AGENTS[0]}"
fi

# 只在交互式终端下 attach。非 TTY 环境 (CI/脚本/嵌套 agent) 下 tmux attach
# 会报 "open terminal failed: not a terminal" 并用退出码 1 污染上游调用者的
# 判断。session 本身已经跑起来了,没必要用 attach 阻塞。
if [ -t 1 ] && [ -t 0 ]; then
  tmux attach -t "$SESSION"
else
  echo "ℹ️  非交互式运行,跳过 tmux attach。查看团队: tmux attach -t $SESSION"
fi
