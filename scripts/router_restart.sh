#!/usr/bin/env bash
# router_restart.sh — emergency router restart inside running tmux pane.
#
# 蒙眼 / 老板手动应急: pkill 之后跑一次,4min 内 router 在 pane 里复活,
# 不留 lark-cli 双订阅孤儿。
#
# 用法 (容器内):
#   bash scripts/router_restart.sh
#
# 行为:
#   1. SIGTERM router pid 文件里的进程 + SIGKILL lark-cli event subscribe 孤儿
#   2. 清空 router pane 残留输入 (Ctrl-C + clear)
#   3. 通过 lib/router_launch.sh 重新拼出 launch 命令并 send-keys 进 router pane
#
# 退出码:
#   0  send-keys 成功 (不保证 router 真的起来 — 调用方应观察 cursor 90s 内更新)
#   非 0  缺前置 (tmux session 不在 / lib/router_launch.sh 异常)
set -euo pipefail

ROOT="$(cd "$(dirname "$0")/.." && pwd)"
cd "$ROOT"

SESSION="${CLAUDETEAM_TMUX_SESSION:-$(python3 -c 'import json; print(json.load(open("team.json"))["session"])' 2>/dev/null || echo)}"
if [ -z "$SESSION" ]; then
  echo "❌ 找不到 tmux session 名 (CLAUDETEAM_TMUX_SESSION 未设且 team.json 不可读)" >&2
  exit 1
fi
if ! tmux has-session -t "$SESSION" 2>/dev/null; then
  echo "❌ tmux session $SESSION 不存在" >&2
  exit 2
fi

STATE_DIR="${CLAUDETEAM_STATE_DIR:-/app/state}"
ROUTER_PID_FILE="$STATE_DIR/router.pid"

echo "[1/3] 杀旧进程 (router pid + lark-cli 孤儿) ..."
if [ -f "$ROUTER_PID_FILE" ]; then
  OLD_PID="$(cat "$ROUTER_PID_FILE" 2>/dev/null || echo)"
  if [ -n "$OLD_PID" ]; then
    kill -TERM "$OLD_PID" 2>/dev/null || true
  fi
fi
sleep 0.5
# pkill -KILL 因为 lark-cli WebSocket 进程对 SIGTERM 经常没反应
pkill -KILL -f "feishu_router.py" 2>/dev/null || true
pkill -KILL -f "lark-cli.*event.*subscribe" 2>/dev/null || true
# fallback: lark-cli 安装路径里 binary 名是 @larksuite/cli 的 npx wrapper
pkill -KILL -f "@larksuite/cli .*event +subscribe" 2>/dev/null || true
sleep 1

echo "[2/3] 清 router pane ..."
tmux send-keys -t "$SESSION:router" C-c 2>/dev/null || true
sleep 0.3
tmux send-keys -t "$SESSION:router" "clear" Enter

echo "[3/3] 重启 router ..."
LAUNCH_CMD="$(bash "$ROOT/scripts/lib/router_launch.sh")"
if [ -z "$LAUNCH_CMD" ]; then
  echo "❌ scripts/lib/router_launch.sh 返回空命令" >&2
  exit 3
fi
tmux send-keys -t "$SESSION:router" "$LAUNCH_CMD" Enter
echo "✅ router 重启命令已注入 tmux $SESSION:router"
echo "   等 90s 验证: ls -l $STATE_DIR/router.cursor (mtime 应当更新)"
