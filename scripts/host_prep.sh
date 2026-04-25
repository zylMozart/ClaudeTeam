#!/usr/bin/env bash
# host_prep.sh — first-up 前预创建凭证目录并 chown 到当前 admin uid。
#
# 为啥需要这个 (F-CRED-2):
#   docker compose 第一次 up 前如果 ./.{kimi,codex,gemini}-credentials 不存在,
#   docker daemon 会用 root:root 自动创建 → admin 后续 cp 进去要 sudo。
#   此脚本在 host 端先 mkdir + chown 当前用户,docker 看到目录已存在不会再
#   接管 owner。
#
# 用法 (项目根 OR 任意位置都行,脚本会 cd 到 repo root):
#   bash scripts/host_prep.sh
#
# 兼容性:
#   老部署不跑此脚本 → 行为完全不变 (旧 root-owned 路径继续走 device-flow)。
#   即此脚本是 opt-in 提速工具,不破坏既有部署。
set -euo pipefail

ROOT="$(cd "$(dirname "$0")/.." && pwd)"
cd "$ROOT"

echo "host_prep.sh — repo root: $ROOT"
echo "  当前用户: $(id -un) (uid=$(id -u) gid=$(id -g))"

DIRS=(
  ".kimi-credentials"
  ".codex-credentials"
  ".gemini-credentials"
  ".lark-cli-credentials"
)

for d in "${DIRS[@]}"; do
  if [ ! -e "$d" ]; then
    mkdir -p "$d"
    echo "  ✅ 创建 $d"
  else
    echo "  ↪️  $d 已存在,跳过 mkdir"
  fi
  chown "$(id -u):$(id -g)" "$d"
  chmod 755 "$d"
done

echo ""
echo "完成。下一步 (按需选择):"
echo "  • 如果 host 已有 ~/.kimi/credentials,可 cp 进去跳过 device-flow:"
echo "      cp ~/.kimi/credentials .kimi-credentials/credentials"
echo "      cp -r ~/.codex/. .codex-credentials/"
echo "      cp -r ~/.gemini/. .gemini-credentials/"
echo "  • 如果你想让容器内写入的文件落到 host 仍是 admin owner,"
echo "    在 .env 里加: HOST_UID=$(id -u)  HOST_GID=$(id -g)"
echo "  • 然后 docker compose up -d"
