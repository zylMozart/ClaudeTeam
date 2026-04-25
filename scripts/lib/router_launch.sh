#!/usr/bin/env bash
# router_launch.sh — print the router launch command (single line) to stdout.
#
# 共享给 docker-entrypoint.sh / router_restart.sh / watchdog 使用,避免三处
# 重复维护启动命令。输出一行可直接 `tmux send-keys` 注入或 bash -c 执行的
# pipeline 字符串: lark-cli event +subscribe ... | python3 feishu_router.py --stdin。
#
# 行为:
#   - 从 scripts/runtime_config.json 读 lark_profile (若存在); 设了就加 --profile
#   - 不带 set -e 因为只 echo 一行,失败时输出空字符串由调用方判断
set -uo pipefail

LARK_PROFILE="$(python3 -c '
import json, os, sys
cfg = "scripts/runtime_config.json"
try:
    with open(cfg) as f:
        print(json.load(f).get("lark_profile") or "")
except Exception:
    print("")
' 2>/dev/null)"

PROFILE_FLAG=""
if [ -n "$LARK_PROFILE" ]; then
  PROFILE_FLAG="--profile $LARK_PROFILE"
fi

echo "npx @larksuite/cli $PROFILE_FLAG event +subscribe --event-types im.message.receive_v1 --compact --quiet --force --as bot | python3 scripts/feishu_router.py --stdin"
