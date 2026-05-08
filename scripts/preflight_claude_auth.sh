#!/usr/bin/env bash
# scripts/preflight_claude_auth.sh — 单次检查 Claude OAuth 凭证状态
#
# exit 0: 健康 (>=60min TTL 或 api_key_mode)
# exit 2: 告警 (<60min TTL)
# exit 3: 过期 / 文件缺失 / 解析失败
#
# 用法:
#   bash scripts/preflight_claude_auth.sh [--cred <path>]
#   CLAUDE_CREDENTIALS_PATH=/path/to/.credentials.json bash scripts/preflight_claude_auth.sh
#
# entrypoint 在拉工作窗口前调用;fail 则不拉 worker + 挂红横幅 (见 §1.3 P0-5)。

set -u

CRED_PATH="${CLAUDE_CREDENTIALS_PATH:-$HOME/.claude/.credentials.json}"
WARN_MIN="${CLAUDETEAM_OAUTH_WARN_MIN:-60}"

while [[ $# -gt 0 ]]; do
  case "$1" in
    --cred) CRED_PATH="$2"; shift 2 ;;
    -h|--help)
      sed -n '2,12p' "$0"; exit 0 ;;
    *) echo "unknown arg: $1" >&2; exit 64 ;;
  esac
done

# API key mode 短路
if [[ -n "${ANTHROPIC_API_KEY:-}" ]]; then
  echo "ok (api_key_mode; skip oauth check)"
  exit 0
fi

if [[ ! -f "$CRED_PATH" ]]; then
  echo "expired (missing credentials file: $CRED_PATH)"
  exit 3
fi

python3 - "$CRED_PATH" "$WARN_MIN" <<'PY'
import json, sys, time, os
path, warn_min = sys.argv[1], int(sys.argv[2])
try:
    with open(path) as f:
        data = json.load(f)
    o = data.get("claudeAiOauth") or {}
    exp = o.get("expiresAt")
    if exp is None:
        print(f"expired (no expiresAt field in {path})")
        sys.exit(3)
    # expiresAt 是毫秒
    exp_s = exp / 1000 if exp > 2e10 else float(exp)
    now = time.time()
    delta_min = (exp_s - now) / 60.0
    if delta_min <= 0:
        print(f"expired ({-delta_min:.0f}min ago; expiresAt={int(exp_s)})")
        sys.exit(3)
    if delta_min < warn_min:
        print(f"warning ({delta_min:.0f}min left; threshold={warn_min})")
        sys.exit(2)
    print(f"ok ({delta_min:.0f}min left)")
    sys.exit(0)
except json.JSONDecodeError as e:
    print(f"expired (json parse failed: {e})")
    sys.exit(3)
except Exception as e:
    print(f"expired (unexpected: {type(e).__name__}: {e})")
    sys.exit(3)
PY
