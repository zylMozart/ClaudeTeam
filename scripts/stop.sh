#!/usr/bin/env bash
# scripts/stop.sh — ClaudeTeam 守护进程 + tmux session 优雅停止脚本
#
# 背景：
#   start-team.sh 把 router / kanban_sync / watchdog 三个守护改成了 nohup
#   后台进程（不在 tmux pane 内），所以 `tmux kill-session` 杀不掉它们。
#   reset.sh 也只删 pid 文件不杀进程，路径还是过期的 scripts/.*.pid。
#   本脚本是这套链路缺失的"软关停"入口。
#
# 行为：
#   1. 读 workspace/shared/state/{watchdog,kanban_sync,router}.pid，对每个 pid
#      先 SIGTERM、最多等 5 秒，仍存活就 SIGKILL；删除 pid 文件
#   2. pkill 兜底：watchdog.py / kanban_sync.py / feishu_router.py /
#      lark-cli 'event +subscribe' 管道残留
#   3. 若 team.json 里的 tmux session 还在，kill-session
#   4. --dry-run 仅打印将要做什么，不实际执行
#
# 退出码：始终 0（即使没东西可杀，也算成功停止）；--dry-run 同样返回 0。
set -uo pipefail

ROOT="$(cd "$(dirname "$0")/.." && pwd)"
cd "$ROOT"

STATE="${CLAUDETEAM_STATE_DIR:-$ROOT/workspace/shared/state}"
DRY_RUN=0

usage() {
  cat <<'EOF'
用法: bash scripts/stop.sh [--dry-run]

选项:
  --dry-run    只打印会停止哪些进程/session，不实际执行
  -h, --help   显示本帮助

观测建议:
  停止后用 bash scripts/health.sh 复核三守护已不在。
EOF
}

while [ $# -gt 0 ]; do
  case "$1" in
    --dry-run|-n) DRY_RUN=1 ;;
    -h|--help) usage; exit 0 ;;
    *) echo "❌ 未知参数: $1" >&2; usage >&2; exit 2 ;;
  esac
  shift
done

# 读 session 名，用于最后一步 tmux kill-session
SESSION=""
if [ -f team.json ]; then
  SESSION=$(python3 -c "import json,sys; print(json.load(open('team.json')).get('session') or '')" 2>/dev/null || echo "")
fi

# stop_pid <label> <pid_file>
#   - 文件不存在 → 跳过
#   - pid 已死 → 仅清理 pid 文件
#   - pid 活 → SIGTERM, 最多等 5 秒; 仍存活则 SIGKILL; 最后删 pid 文件
stop_pid() {
  local label="$1" pid_file="$2"
  if [ ! -f "$pid_file" ]; then
    echo "  $label: pid 文件不存在 ($pid_file) — 跳过"
    return 0
  fi
  local pid
  pid="$(tr -d '[:space:]' < "$pid_file" 2>/dev/null || true)"
  if [ -z "$pid" ]; then
    echo "  $label: pid 文件为空 — 删除"
    if [ "$DRY_RUN" -eq 0 ]; then rm -f "$pid_file"; fi
    return 0
  fi
  if ! kill -0 "$pid" 2>/dev/null; then
    echo "  $label: pid=$pid 进程已不存在 — 仅清理 pid 文件"
    if [ "$DRY_RUN" -eq 0 ]; then rm -f "$pid_file"; fi
    return 0
  fi
  if [ "$DRY_RUN" -eq 1 ]; then
    echo "  [dry-run] $label: pid=$pid → SIGTERM (≤5s 等待) → 必要时 SIGKILL → rm $pid_file"
    return 0
  fi
  echo "  $label: pid=$pid → SIGTERM"
  kill -TERM "$pid" 2>/dev/null || true
  local waited=0
  while [ "$waited" -lt 5 ] && kill -0 "$pid" 2>/dev/null; do
    sleep 1
    waited=$((waited + 1))
  done
  if kill -0 "$pid" 2>/dev/null; then
    echo "  $label: pid=$pid 5s 后仍存活 → SIGKILL"
    kill -KILL "$pid" 2>/dev/null || true
    sleep 1
    if kill -0 "$pid" 2>/dev/null; then
      echo "  $label: ⚠️ pid=$pid SIGKILL 后仍存在 (僵尸/不可中断)，留 pid 文件供排查"
      return 0
    fi
    echo "  $label: pid=$pid 已强杀"
  else
    echo "  $label: pid=$pid 已优雅退出 (${waited}s)"
  fi
  rm -f "$pid_file"
}

# pkill_fallback <pattern>
#   先 -TERM, 1 秒后还在再 -KILL。dry-run 只打印。
pkill_fallback() {
  local pat="$1"
  local matched
  matched=$(pgrep -f -- "$pat" 2>/dev/null | wc -l | tr -d ' ')
  matched=${matched:-0}
  if [ "$matched" -eq 0 ]; then
    echo "  '$pat': 无残留"
    return 0
  fi
  if [ "$DRY_RUN" -eq 1 ]; then
    echo "  [dry-run] '$pat': 发现 $matched 个进程，会 pkill -TERM (必要时 -KILL)"
    return 0
  fi
  echo "  '$pat': 发现 $matched 个残留进程 → SIGTERM"
  pkill -TERM -f -- "$pat" 2>/dev/null || true
  sleep 1
  if pgrep -f -- "$pat" >/dev/null 2>&1; then
    pkill -KILL -f -- "$pat" 2>/dev/null || true
    sleep 1
    if pgrep -f -- "$pat" >/dev/null 2>&1; then
      echo "  '$pat': ⚠️ SIGKILL 后仍有残留"
    else
      echo "  '$pat': 已强杀"
    fi
  else
    echo "  '$pat': 已优雅退出"
  fi
}

echo "🛑 ClaudeTeam stop"
if [ "$DRY_RUN" -eq 1 ]; then
  echo "   (dry-run，不会实际停止任何进程)"
fi
echo "   STATE=$STATE"
echo "   session=${SESSION:-<unknown>}"
echo ""

echo "[1/3] 优雅停止守护进程 (pid 文件)"
stop_pid "watchdog   " "$STATE/watchdog.pid"
stop_pid "kanban_sync" "$STATE/kanban_sync.pid"
stop_pid "router     " "$STATE/router.pid"

echo ""
echo "[2/3] pkill 兜底残留进程"
pkill_fallback "watchdog.py"
pkill_fallback "kanban_sync.py"
pkill_fallback "feishu_router.py"
# lark-cli 订阅管道：被 reparent 到 init 的孤儿不会出现在上面三个 pid 文件里
pkill_fallback "event +subscribe --event-types im.message.receive_v1"

echo ""
echo "[3/3] 关闭 tmux session"
if [ -z "$SESSION" ]; then
  echo "  team.json 未提供 session 名 — 跳过"
elif ! command -v tmux >/dev/null 2>&1; then
  echo "  tmux 未安装 — 跳过"
elif ! tmux has-session -t "$SESSION" 2>/dev/null; then
  echo "  tmux session '$SESSION' 不存在 — 跳过"
else
  if [ "$DRY_RUN" -eq 1 ]; then
    echo "  [dry-run] tmux kill-session -t $SESSION"
  else
    tmux kill-session -t "$SESSION"
    echo "  tmux session '$SESSION' 已关闭"
  fi
fi

echo ""
if [ "$DRY_RUN" -eq 1 ]; then
  echo "✅ dry-run 完成。去掉 --dry-run 真正执行。"
else
  echo "✅ stop 完成。复核: bash scripts/health.sh"
fi
