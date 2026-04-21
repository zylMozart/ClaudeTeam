#!/usr/bin/env bash
# scripts/relaunch_as_codex.sh — 单 agent 引擎替换：Claude Code → Codex CLI
#
# 用法:
#   bash scripts/relaunch_as_codex.sh <agent> [--force] [--skip-init]
#     --force      即使 agent 在忙也切 (默认: 忙则拒切)
#     --skip-init  不发 identity.md init_msg (手动冷启动)
#
# 做什么 (严格顺序):
#   1. 校验 tmux 窗口 server-manager:<agent> 存在
#   2. 校验 team.json 有该 agent
#   3. 忙态检查 — pane 末尾看到 "esc to interrupt" 拒切 (除非 --force)
#   4. 状态表写 "引擎切换中" — 同 suspend_agent 顺序，防 router 丢消息
#   5. 保存当前 CC session_id (便于回滚)
#   6. 优雅关闭 claude：发 "/exit" + Enter；1s 后兜底 SIGTERM pane 子进程
#   7. team.json 写 agents.<name>.cli = "codex-cli"
#   8. 启动 codex --dangerously-bypass-approvals-and-sandbox
#   9. 处理 onboarding: Press-enter-to-continue / trust dialog → 自动回 Enter / "1"
#  10. 轮询 ready marker 出现
#  11. 注入 init_msg (identity.md + inbox 检查)
#  12. 状态表改 "待命"
#
# 回滚:
#   bash scripts/lib/agent_lifecycle.sh suspend <agent>
#   # 改回 team.json agents.<name>.cli = "claude-code"
#   bash scripts/lib/agent_lifecycle.sh wake <agent>
#
# 退出码:
#   0 切换成功
#   1 切换失败 (见 stderr)
#   2 用法错误
#   3 agent 在忙且无 --force
set -euo pipefail

SCRIPTS_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_ROOT="$(cd "$SCRIPTS_DIR/.." && pwd)"
TEAM_JSON="$PROJECT_ROOT/team.json"

# ── 参数解析 ──────────────────────────────────────────────────
FORCE=0
SKIP_INIT=0
AGENT=""
for arg in "$@"; do
  case "$arg" in
    --force)     FORCE=1 ;;
    --skip-init) SKIP_INIT=1 ;;
    -*)
      echo "❌ 未知参数: $arg" >&2
      echo "用法: $0 <agent> [--force] [--skip-init]" >&2
      exit 2
      ;;
    *)
      if [[ -z "$AGENT" ]]; then
        AGENT="$arg"
      else
        echo "❌ 多个 agent 名: $AGENT, $arg" >&2
        exit 2
      fi
      ;;
  esac
done

if [[ -z "$AGENT" ]]; then
  echo "用法: $0 <agent> [--force] [--skip-init]" >&2
  exit 2
fi

if ! [[ "$AGENT" =~ ^[A-Za-z0-9_-]+$ ]]; then
  echo "❌ 非法 agent 名: $AGENT" >&2
  exit 2
fi

# ── 工具函数 ──────────────────────────────────────────────────
log() { echo "[$(date '+%H:%M:%S')] $*"; }

tmux_session() {
  python3 -c "import json; print(json.load(open('$TEAM_JSON'))['session'])" 2>/dev/null \
    || echo "server-manager"
}

agent_in_team_json() {
  python3 -c "
import json, sys
t = json.load(open('$TEAM_JSON'))
sys.exit(0 if '$AGENT' in t.get('agents', {}) else 1)
" 2>/dev/null
}

capture_pane() {
  tmux capture-pane -t "$SESSION:$AGENT" -p -S -"${1:-80}" 2>/dev/null || true
}

pane_is_busy() {
  local buf
  buf=$(capture_pane 30)
  # Claude Code 或 Codex CLI 都用 "esc to interrupt" 表示 busy
  [[ "$buf" == *"esc to interrupt"* ]]
}

pane_pid() {
  tmux display-message -t "$SESSION:$AGENT" -p '#{pane_pid}' 2>/dev/null
}

# 等待 pane 里出现某个子串 (regex 或 literal), 超时返回 1
wait_for_pane_match() {
  local pattern="$1"
  local timeout="${2:-30}"
  local deadline=$(( $(date +%s) + timeout ))
  while (( $(date +%s) < deadline )); do
    if capture_pane 80 | grep -qE "$pattern"; then
      return 0
    fi
    sleep 1
  done
  return 1
}

# 把 team.json agents.<name>.cli 改成目标值 (atomic via os.replace)
set_team_cli() {
  local cli="$1"
  AGENT="$AGENT" CLI="$cli" TEAM_JSON="$TEAM_JSON" python3 - <<'PY'
import json, os
p = os.environ["TEAM_JSON"]
with open(p) as f:
    t = json.load(f)
agent = os.environ["AGENT"]
t.setdefault("agents", {}).setdefault(agent, {})
t["agents"][agent]["cli"] = os.environ["CLI"]
tmp = p + ".tmp"
with open(tmp, "w") as f:
    json.dump(t, f, indent=2, ensure_ascii=False)
    f.write("\n")
os.replace(tmp, p)
PY
}

# ── 预检 ──────────────────────────────────────────────────────
SESSION=$(tmux_session)
log "🎯 agent=$AGENT  session=$SESSION  force=$FORCE  skip_init=$SKIP_INIT"

if ! tmux has-session -t "$SESSION:$AGENT" 2>/dev/null; then
  echo "❌ tmux 窗口 $SESSION:$AGENT 不存在 — 先 /hire 或 spawn_agent" >&2
  exit 1
fi

if ! agent_in_team_json; then
  echo "❌ $AGENT 不在 team.json 中" >&2
  exit 1
fi

if pane_is_busy; then
  if (( FORCE == 0 )); then
    echo "⚠️ $AGENT 当前在忙 (pane 末尾检测到 'esc to interrupt')" >&2
    echo "   想强制切换加 --force" >&2
    exit 3
  fi
  log "⚠️ $AGENT 在忙但 --force 生效，继续"
fi

# ── 1. 状态表: 引擎切换中 ─────────────────────────────────────
log "📋 写状态表: 引擎切换中"
if ! python3 "$SCRIPTS_DIR/feishu_msg.py" status "$AGENT" 休眠 \
     "引擎切换: claude-code → codex-cli" >/dev/null 2>&1; then
  echo "⚠️ 状态表写入失败 — 继续切换但 router 可能丢消息" >&2
fi

# ── 2. 保存 CC session_id (回滚用) ────────────────────────────
log "💾 保存当前 CC session_id (便于回滚)"
# 复用 agent_lifecycle.sh 的 discover/save 函数
source "$SCRIPTS_DIR/lib/agent_lifecycle.sh"
OLD_SID=$(_lifecycle_discover_session "$AGENT" || true)
if [[ -n "$OLD_SID" ]]; then
  _lifecycle_save_session "$AGENT" "$OLD_SID"
  log "   saved CC sid=${OLD_SID:0:8}"
else
  log "   ⚠️ 未找到 CC session_id — 回滚只能冷启动"
fi

# ── 3. 优雅退出 claude ────────────────────────────────────────
log "🛑 关闭 claude 会话"
# 先尝试软退: /exit + Enter (claude code TUI 支持)
tmux send-keys -t "$SESSION:$AGENT" "/exit" Enter 2>/dev/null || true
sleep 2
# 再送 C-c 兜底 (如果 TUI 没响应)
tmux send-keys -t "$SESSION:$AGENT" C-c 2>/dev/null || true
sleep 1
# 最后 SIGTERM 所有 claude 子进程
PIDS=$(_lifecycle_pids_for_agent "$AGENT" || true)
if [[ -n "$PIDS" ]]; then
  log "   SIGTERM claude pids: $PIDS"
  kill -TERM $PIDS 2>/dev/null || true
  sleep 1
  kill -KILL $PIDS 2>/dev/null || true
fi

# 确认 bash prompt 回来 (pane 末尾没有 TUI 渲染)
if wait_for_pane_match '\$\s*$|#\s*$' 10; then
  log "   bash prompt 已回归"
else
  log "   ⚠️ bash prompt 未检测到 — 继续但可能脏"
fi

# ── 4. 切 team.json cli 字段 ──────────────────────────────────
log "📝 team.json: agents.$AGENT.cli = codex-cli"
set_team_cli "codex-cli"

# ── 5. 启动 codex ─────────────────────────────────────────────
log "🚀 启动 codex"
SPAWN_CMD=$(python3 "$SCRIPTS_DIR/cli_adapters/resolve.py" "$AGENT" spawn_cmd "" 2>/dev/null) || {
  echo "❌ 解析 codex spawn_cmd 失败" >&2
  exit 1
}
tmux send-keys -t "$SESSION:$AGENT" "$SPAWN_CMD" Enter

# ── 6. 处理 onboarding ────────────────────────────────────────
# 首次运行每个场景:
#   (a) "Press enter to continue" 介绍页 (只在该机器/用户首次用 codex 时)
#   (b) "Do you trust the contents of this directory?" (只在该目录首次用时)
# 两者用 sqlite 持久化；除非删 ~/.codex/state_5.sqlite 否则第二次起直接跳过
log "⏳ 处理 codex onboarding (press-enter / trust dialog)"
for attempt in 1 2 3 4; do
  sleep 3
  buf=$(capture_pane 80)
  if [[ "$buf" == *"Press enter to continue"* ]]; then
    log "   检测到 'Press enter to continue' → 回 Enter"
    tmux send-keys -t "$SESSION:$AGENT" Enter
    continue
  fi
  if [[ "$buf" == *"Do you trust the contents of this directory"* ]]; then
    log "   检测到 trust dialog → 回 '1' + Enter"
    tmux send-keys -t "$SESSION:$AGENT" "1" Enter
    continue
  fi
  # 出现 prompt 占位符 = 已就绪
  if [[ "$buf" == *"Implement {feature}"* ]] || \
     [[ "$buf" == *"tab to queue message"* ]] || \
     [[ "$buf" == *"gpt-5"* ]]; then
    log "   codex TUI 已就绪"
    break
  fi
done

# 最终 ready check
if ! wait_for_pane_match 'tab to queue message|gpt-[45]|Implement \{feature\}' 20; then
  echo "❌ codex 未在预期时间内就绪，pane 末尾:" >&2
  capture_pane 20 | tail -15 >&2
  echo "   手动检查后再决定回滚与否" >&2
  exit 1
fi

# ── 7. 注入 init_msg ──────────────────────────────────────────
if (( SKIP_INIT == 0 )); then
  log "📨 注入 init_msg (identity.md + inbox 检查)"
  INIT_MSG="你是团队的 ${AGENT}。
【必读】agents/${AGENT}/identity.md — 了解角色和通讯规范。
【立即执行】
1. python3 scripts/feishu_msg.py inbox ${AGENT}   # 看收件箱
2. python3 scripts/feishu_msg.py status ${AGENT} 进行中 \"引擎已切换到 codex-cli，初始化完成\"
准备好后一句话汇报: 你是谁 · 当前状态 · 有无未读消息 · 使用的 CLI (codex)。"
  # -l 字面模式避免 tmux 把中文解析成按键
  tmux send-keys -l -t "$SESSION:$AGENT" "$INIT_MSG"
  sleep 0.5
  tmux send-keys -t "$SESSION:$AGENT" Enter
  log "   init_msg 已发送"
else
  log "⏭️ --skip-init 生效，跳过 init_msg"
fi

# ── 8. 状态表: 待命 ──────────────────────────────────────────
python3 "$SCRIPTS_DIR/feishu_msg.py" status "$AGENT" 待命 \
  "引擎切换完成: codex-cli" >/dev/null 2>&1 || true

log "✅ $AGENT 已切换到 codex-cli"
log "   回滚: 改回 team.json cli=claude-code + bash $SCRIPTS_DIR/lib/agent_lifecycle.sh wake $AGENT"
log "   观察: tmux capture-pane -t $SESSION:$AGENT -p 或 /tmux $AGENT"
