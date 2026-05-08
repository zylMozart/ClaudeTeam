#!/usr/bin/env bash
# scripts/claude_token_guard.sh — Claude OAuth token TTL 守护
#
# 容器内后台常驻。每 $INTERVAL 秒 (默认 1800s = 30min) 巡一次:
#   - 读 .credentials.json → 计算 TTL
#   - 写 /app/state/claude_token_status.json (供 /usage / watchdog 读)
#   - TTL < WARN_MIN → manager inbox 发 ⚠️
#   - 已过期            → manager inbox 发 🚨 (高优)
# 本脚本只检测 + 通知，不刷 token (device-flow 需浏览器，容器内不可做)。
#
# 用法:
#   bash scripts/claude_token_guard.sh                # 常驻循环
#   bash scripts/claude_token_guard.sh --once         # 单次执行 (自测)
#
# 环境变量:
#   CLAUDE_CREDENTIALS_PATH   default: $HOME/.claude/.credentials.json
#   CLAUDETEAM_STATE_DIR      default: /app/state
#   CLAUDETEAM_OAUTH_WARN_MIN default: 60
#   CLAUDETEAM_OAUTH_GUARD_INTERVAL default: 1800
#   CLAUDETEAM_PROJECT_ROOT   default: /app (用于调 feishu_msg.py)

set -u

ROOT="${CLAUDETEAM_PROJECT_ROOT:-/app}"
CRED_PATH="${CLAUDE_CREDENTIALS_PATH:-$HOME/.claude/.credentials.json}"
STATE_DIR="${CLAUDETEAM_STATE_DIR:-/app/state}"
STATE_FILE="$STATE_DIR/claude_token_status.json"
WARN_MIN="${CLAUDETEAM_OAUTH_WARN_MIN:-60}"
INTERVAL="${CLAUDETEAM_OAUTH_GUARD_INTERVAL:-1800}"
ONCE=0
[[ "${1:-}" == "--once" ]] && ONCE=1

mkdir -p "$STATE_DIR"

log() { echo "[$(date '+%F %T')] claude_token_guard: $*"; }

notify_manager() {
  local priority="$1"; local msg="$2"
  if [[ -x "$ROOT/scripts/feishu_msg.py" ]] || [[ -f "$ROOT/scripts/feishu_msg.py" ]]; then
    ( cd "$ROOT" && python3 scripts/feishu_msg.py send manager claude_token_guard "$msg" "$priority" ) \
      >/dev/null 2>&1 || log "warn: feishu_msg send failed"
  else
    log "warn: feishu_msg.py not found at $ROOT/scripts/"
  fi
}

check_once() {
  # API-key mode 短路
  if [[ -n "${ANTHROPIC_API_KEY:-}" ]]; then
    python3 - "$STATE_FILE" <<'PY'
import json, sys, time
json.dump({
    "status": "api_key_mode",
    "expires_at": None,
    "minutes_left": None,
    "last_check": int(time.time()),
    "note": "ANTHROPIC_API_KEY is set; oauth guard short-circuited",
}, open(sys.argv[1], "w"))
PY
    log "ok (api_key_mode)"
    return 0
  fi

  if [[ ! -f "$CRED_PATH" ]]; then
    python3 - "$STATE_FILE" "$CRED_PATH" <<'PY'
import json, sys, time
json.dump({
    "status": "expired",
    "expires_at": None,
    "minutes_left": None,
    "last_check": int(time.time()),
    "note": f"credentials file missing: {sys.argv[2]}",
}, open(sys.argv[1], "w"))
PY
    log "🚨 credentials missing at $CRED_PATH"
    notify_manager "高" "🚨 Claude OAuth 凭证缺失: $CRED_PATH — 新 claude 进程将全部 401，请 host 侧重放凭证"
    return 3
  fi

  # 解析并写 state；脚本返回判定结果供 shell 路由
  python3 - "$CRED_PATH" "$STATE_FILE" "$WARN_MIN" <<'PY'
import json, sys, time
cred_path, state_path, warn_min = sys.argv[1], sys.argv[2], int(sys.argv[3])
status, exp_ts, left_min, note = "expired", None, None, ""
try:
    with open(cred_path) as f:
        data = json.load(f)
    o = data.get("claudeAiOauth") or {}
    exp = o.get("expiresAt")
    if exp is None:
        note = "no expiresAt field"
    else:
        exp_ts = int(exp / 1000 if exp > 2e10 else exp)
        now = int(time.time())
        left_min = int((exp_ts - now) / 60)
        if left_min <= 0:
            status = "expired"
            note = f"{-left_min}min ago"
        elif left_min < warn_min:
            status = "warning"
            note = f"{left_min}min left (threshold={warn_min})"
        else:
            status = "ok"
            note = f"{left_min}min left"
except json.JSONDecodeError as e:
    note = f"json parse failed: {e}"
except Exception as e:
    note = f"{type(e).__name__}: {e}"

json.dump({
    "status": status,
    "expires_at": exp_ts,
    "minutes_left": left_min,
    "last_check": int(time.time()),
    "note": note,
}, open(state_path, "w"))
print(f"{status}|{left_min if left_min is not None else ''}|{note}")
PY
  local status; status=$(python3 -c "import json; print(json.load(open('$STATE_FILE'))['status'])" 2>/dev/null || echo "expired")
  local left; left=$(python3 -c "import json; v=json.load(open('$STATE_FILE'))['minutes_left']; print(v if v is not None else '')" 2>/dev/null || echo "")
  case "$status" in
    ok)
      log "ok (${left}min left)"
      ;;
    warning)
      log "⚠️ warning: ${left}min left"
      notify_manager "高" "⚠️ Claude OAuth token 将在 ${left} 分钟内过期，请 host 侧执行 \`claude /login\` 刷新"
      ;;
    expired)
      log "🚨 expired"
      notify_manager "高" "🚨 Claude OAuth token 已过期，所有新 claude 进程将 401；host 侧 \`claude /login\` 后重放凭证"
      ;;
    api_key_mode)
      log "ok (api_key_mode)"
      ;;
    *)
      log "unknown status=$status"
      ;;
  esac
}

if [[ "$ONCE" == "1" ]]; then
  check_once
  exit 0
fi

log "starting loop (interval=${INTERVAL}s, warn_min=${WARN_MIN})"
while true; do
  check_once || true
  sleep "$INTERVAL"
done
