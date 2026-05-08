#!/usr/bin/env bash
# scripts/supervisor_apply.sh — 读 supervisor 决策 jsonl,执行未 apply 的 SUSPEND
#
# 运行时机: supervisor_ticker while-sleep 每轮 tick 之后紧跟一次。
# 职责:
#   - 读 agents/supervisor/workspace/decisions/$(date +%F).jsonl
#   - 找 action=SUSPEND 且无 applied_at 的行
#   - 对每行调 suspend_agent <agent>,成功后在行末回填 applied_at
#   - 白名单 agent (overrides.json 的 never_suspend) 一律 skip + 打 warn
# 决策/执行解耦: supervisor 自己不调 suspend_agent (见方案 §2.2.2)
#
# 并发保护: flock -w 10 /tmp/supervisor_apply.lock
#
# 用法:
#   bash scripts/supervisor_apply.sh           # 一次性执行,退出
#   bash scripts/supervisor_apply.sh --date YYYY-MM-DD  # 重放指定日期

set -u

ROOT="$(cd "$(dirname "$0")/.." && pwd)"
cd "$ROOT"

DATE="$(date +%F)"
while [[ $# -gt 0 ]]; do
  case "$1" in
    --date) DATE="$2"; shift 2 ;;
    -h|--help) sed -n '2,18p' "$0"; exit 0 ;;
    *) echo "unknown arg: $1" >&2; exit 64 ;;
  esac
done

WS="$ROOT/agents/supervisor/workspace"
DECISIONS="$WS/decisions/$DATE.jsonl"
OVERRIDES="$WS/overrides.json"
LOCK="/tmp/supervisor_apply.lock"

log() { echo "[$(date '+%F %T')] supervisor_apply: $*"; }

if [[ ! -f "$DECISIONS" ]]; then
  log "no decisions file for $DATE (ok, skip)"
  exit 0
fi

(
  if ! flock -w 10 9; then
    log "flock timeout (another apply still running); skip this round"
    exit 0
  fi

  source "$ROOT/scripts/lib/agent_lifecycle.sh"

  # 读白名单
  whitelist_arr=()
  if [[ -f "$OVERRIDES" ]]; then
    mapfile -t whitelist_arr < <(python3 -c "
import json
try:
    d = json.load(open('$OVERRIDES'))
    for n in d.get('never_suspend', []): print(n)
except Exception: pass
")
  fi

  in_whitelist() {
    local name="$1"
    for w in "${whitelist_arr[@]}"; do
      [[ "$w" == "$name" ]] && return 0
    done
    return 1
  }

  # 逐行扫,找未 apply 的 SUSPEND,apply 后把整份文件改写一次
  tmpfile="$(mktemp)"
  applied=0; skipped=0; kept=0; total=0
  while IFS= read -r line || [[ -n "$line" ]]; do
    total=$((total+1))
    if [[ -z "$line" ]]; then
      echo "" >> "$tmpfile"
      continue
    fi
    # 解析
    parsed="$(python3 -c "
import json, sys
try:
    d = json.loads(sys.argv[1])
    print('|'.join([
        str(d.get('action', '')),
        str(d.get('agent', '')),
        '1' if d.get('applied_at') else '0',
    ]))
except Exception:
    print('PARSE_ERR||')
" "$line" 2>/dev/null || echo "PARSE_ERR||")"
    action="${parsed%%|*}"; rest="${parsed#*|}"
    agent="${rest%%|*}"; already="${rest#*|}"

    if [[ "$action" != "SUSPEND" ]] || [[ "$already" == "1" ]]; then
      echo "$line" >> "$tmpfile"
      [[ "$action" == "KEEP" ]] && kept=$((kept+1))
      continue
    fi

    if [[ -z "$agent" ]]; then
      log "⚠️ skip malformed line (no agent): $line"
      echo "$line" >> "$tmpfile"
      continue
    fi

    if in_whitelist "$agent"; then
      log "⚠️ skip $agent (in never_suspend whitelist) — supervisor should not have decided this"
      # 标 applied_at 但也标 skip 理由,防止下一轮再扫
      new_line="$(python3 -c "
import json, sys, time
d = json.loads(sys.argv[1])
d['applied_at'] = int(time.time())
d['apply_skip'] = 'whitelist'
print(json.dumps(d, ensure_ascii=False))
" "$line")"
      echo "$new_line" >> "$tmpfile"
      skipped=$((skipped+1))
      continue
    fi

    log "→ suspend_agent $agent"
    if suspend_agent "$agent" >/tmp/sa_$$.out 2>&1; then
      applied=$((applied+1))
      new_line="$(python3 -c "
import json, sys, time
d = json.loads(sys.argv[1])
d['applied_at'] = int(time.time())
d['apply_result'] = 'ok'
print(json.dumps(d, ensure_ascii=False))
" "$line")"
      echo "$new_line" >> "$tmpfile"
    else
      log "⚠️ suspend_agent $agent failed: $(tail -3 /tmp/sa_$$.out)"
      new_line="$(python3 -c "
import json, sys, time
d = json.loads(sys.argv[1])
d['applied_at'] = int(time.time())
d['apply_result'] = 'fail'
d['apply_error'] = sys.argv[2][:200]
print(json.dumps(d, ensure_ascii=False))
" "$line" "$(cat /tmp/sa_$$.out)")"
      echo "$new_line" >> "$tmpfile"
      skipped=$((skipped+1))
    fi
    rm -f /tmp/sa_$$.out
  done < "$DECISIONS"

  mv "$tmpfile" "$DECISIONS"
  log "done: applied=$applied skipped=$skipped kept=$kept total=$total"
) 9>"$LOCK"
