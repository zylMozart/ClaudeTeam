#!/bin/bash
# scripts/reset.sh — ClaudeTeam 完全清理脚本
#
# 用途: 把一个 ClaudeTeam 部署回退到"git clone 刚出"的状态,以便重新 init。
#       对应 P1-11 "完全清理重来"流程。
#
# 三层可选清理,由 flag 控制:
#   [1] 运行时状态 (默认会动, 只要 --yes):
#         - docker compose down -v (容器 + 未命名 volume)
#         - scripts/runtime_config.json
#         - scripts/.router.pid / .kanban_sync.pid / .watchdog.pid / .router.cursor
#   [2] 飞书云端资源 (默认会动, 只要 --yes):
#         - Bitable app    → drive +delete --type bitable
#         - 群聊          → lark-cli 不支持 dismiss, 打印 chat_id + 手动解散指引
#   [3] 用户产出 (--nuke 才动):
#         - workspace/*
#         - agents/*/workspace/, agents/*/core_memory.md, agents/*/identity.md
#         - team.json
#
# 安全设计:
#   - 默认 dry-run, 只打印要动的东西, 不触碰任何状态
#   - 真删要求 --yes + 键入 session 名做二次确认
#   - Bitable / 文件删除失败不中断后续清理, 但会黄字警告, 让人自行决定是否追跑
#
# 用法示例:
#   scripts/reset.sh              # 安全预览
#   scripts/reset.sh --yes        # 真删, 保留 team.json 和 workspace
#   scripts/reset.sh --yes --nuke # 真删 + 抹掉用户产出, 等同 git clean 级别

set -e
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
cd "$ROOT"

DRY_RUN=1
NUKE=0

red()    { printf '\033[31m%s\033[0m\n' "$*"; }
green()  { printf '\033[32m%s\033[0m\n' "$*"; }
yellow() { printf '\033[33m%s\033[0m\n' "$*"; }
cyan()   { printf '\033[36m%s\033[0m\n' "$*"; }
note()   { echo "   $*"; }

usage() {
  cat <<'EOF'
用法: scripts/reset.sh [--yes] [--nuke] [--help]

默认 dry-run, 只打印会清理什么, 不动任何东西。

选项:
  --yes       实际执行。会要求键入 session 名二次确认。
  --nuke      同时删除 workspace/、agents/*/workspace、agents/*/identity.md、team.json
              等用户产出。极度危险, 必须和 --yes 一起用。
  --help      显示本帮助。

推荐流程:
  1. 先跑不带 flag 的 dry-run, 看清要动什么
  2. 确认后追加 --yes
  3. 想彻底重置到 git clone 状态, 再追加 --nuke
EOF
}

while [ $# -gt 0 ]; do
  case "$1" in
    --yes)    DRY_RUN=0 ;;
    --nuke)   NUKE=1 ;;
    --help|-h) usage; exit 0 ;;
    *) red "❌ 未知参数: $1"; usage; exit 2 ;;
  esac
  shift
done

# ── 读取当前部署信息 ─────────────────────────────────────────
# 三个字段用 python3 从 runtime_config.json 和 team.json 里取, 任何一个读失败
# 都只影响对应的清理步骤, 不影响别的。用 python 而不是 jq, 因为项目已经强依赖
# python3, 不想再增加一个 jq 依赖。
RUNTIME=scripts/runtime_config.json
SESSION=""
BITABLE=""
CHAT_ID=""
LARK_PROFILE=""

if [ -s "$RUNTIME" ] && python3 -c "import json; json.load(open('$RUNTIME'))" 2>/dev/null; then
  BITABLE=$(python3 -c "import json; print(json.load(open('$RUNTIME')).get('bitable_app_token') or '')" 2>/dev/null || echo "")
  CHAT_ID=$(python3 -c "import json; print(json.load(open('$RUNTIME')).get('chat_id') or '')" 2>/dev/null || echo "")
  LARK_PROFILE=$(python3 -c "import json; print(json.load(open('$RUNTIME')).get('lark_profile') or '')" 2>/dev/null || echo "")
fi
if [ -f team.json ]; then
  SESSION=$(python3 -c "import json; print(json.load(open('team.json')).get('session') or '')" 2>/dev/null || echo "")
fi

if [ $DRY_RUN -eq 1 ]; then
  cyan "🔍 ClaudeTeam reset (DRY-RUN, 未传 --yes)"
else
  cyan "🔥 ClaudeTeam reset (真删模式)"
fi
echo ""
echo "探测到的当前部署:"
note "session:        ${SESSION:-<unknown>}"
note "bitable token:  ${BITABLE:-<unknown>}"
note "chat_id:        ${CHAT_ID:-<unknown>}"
note "lark profile:   ${LARK_PROFILE:-<unknown>}"
echo ""

# ── 真跑前的二次确认 ─────────────────────────────────────────
# 策略: 要求输入当前 session 名, 防手抖。session 未知时回落到输入 'RESET' 字面量。
if [ $DRY_RUN -eq 0 ]; then
  yellow "⚠️  即将清理上述部署。"
  [ $NUKE -eq 1 ] && red "   --nuke 已启用: workspace/ 和 agents/ 里的用户产出也会删!"
  echo ""
  if [ -n "$SESSION" ]; then
    read -r -p "请键入 session 名 '$SESSION' 确认 (回车取消): " CONFIRM
    if [ "$CONFIRM" != "$SESSION" ]; then
      red "❌ 确认失败, 未执行任何删除。"
      exit 1
    fi
  else
    read -r -p "未能从 runtime_config/team.json 读到 session, 键入 'RESET' 强制确认: " CONFIRM
    if [ "$CONFIRM" != "RESET" ]; then
      red "❌ 确认失败, 未执行任何删除。"
      exit 1
    fi
  fi
fi

# ── helper: dry-run 时只打印, 真跑时执行 ─────────────────────
run() {
  if [ $DRY_RUN -eq 1 ]; then
    cyan "   [dry-run] $*"
  else
    echo "   $ $*"
    "$@"
  fi
}

remove() {
  local target="$1"
  [ -e "$target" ] || return 0
  if [ $DRY_RUN -eq 1 ]; then
    cyan "   [dry-run] rm -rf $target"
  else
    echo "   rm -rf $target"
    rm -rf "$target"
  fi
}

# ── [1/5] docker compose down ────────────────────────────────
echo ""
cyan "[1/5] 停止 docker compose 容器"
if ! command -v docker >/dev/null 2>&1; then
  yellow "   跳过: 宿主机没有 docker CLI (host-native 部署不需要这一步)"
elif [ ! -f docker-compose.yml ]; then
  yellow "   跳过: 当前目录没有 docker-compose.yml"
else
  # down -v 会同时清理 docker-compose.yml 里定义的 named volume。
  # 目前 claudeteam-agents / claudeteam-workspace 是空定义未实际使用 (数据走
  # bind mount), 所以 -v 不会吃掉宿主机上的 agents/workspace 目录内容, 安全。
  run docker compose down -v
fi

# ── [2/5] 运行时状态文件 ─────────────────────────────────────
echo ""
cyan "[2/5] 清理运行时状态文件"
for f in \
    scripts/.router.pid \
    scripts/.kanban_sync.pid \
    scripts/.watchdog.pid \
    scripts/.router.cursor \
    scripts/runtime_config.json; do
  remove "$f"
done

# ── [3/5] 飞书 Bitable ───────────────────────────────────────
echo ""
cyan "[3/5] 删除飞书 Bitable"
if [ -z "$BITABLE" ]; then
  yellow "   跳过: runtime_config.json 里没 bitable_app_token"
else
  # 用 --as bot, 因为 setup.py 里创建 Bitable 走的也是 bot 身份, 所有权统一。
  # lark-cli 的 drive +delete 自带 --yes 才真删; 我们在 dry-run 下不传 --yes。
  PROFILE_FLAG=""
  [ -n "$LARK_PROFILE" ] && PROFILE_FLAG="--profile $LARK_PROFILE"
  if [ $DRY_RUN -eq 1 ]; then
    cyan "   [dry-run] npx @larksuite/cli $PROFILE_FLAG drive +delete --file-token $BITABLE --type bitable --yes --as bot"
  else
    echo "   $ npx @larksuite/cli $PROFILE_FLAG drive +delete --file-token $BITABLE --type bitable --yes --as bot"
    set +e
    npx @larksuite/cli $PROFILE_FLAG drive +delete --file-token "$BITABLE" --type bitable --yes --as bot
    rc=$?
    set -e
    if [ $rc -ne 0 ]; then
      yellow "   ⚠️  drive +delete 返回 $rc (可能权限不足 / Bitable 已被别人删 / 网络问题)"
      yellow "       本脚本继续往下跑, 你可手动在飞书云文档回收站确认。"
    fi
  fi
fi

# ── [4/5] 飞书群聊 (仅提示) ──────────────────────────────────
echo ""
cyan "[4/5] 飞书群聊"
if [ -z "$CHAT_ID" ]; then
  yellow "   跳过: 没 chat_id"
else
  # lark-cli 的 im chats 子命令没有 dismiss/delete, 只有 create/get/link/list/update。
  # 为避免误导用户以为脚本已经把群聊清掉了, 这里强制打印手动步骤。
  yellow "   ⚠️  lark-cli 不支持通过 API 解散群聊, 需在飞书 App 内手动操作:"
  echo "        1. 打开群 '🤖 ${SESSION:-?} 协作团队' (chat_id=$CHAT_ID)"
  echo "        2. 右上角 ⋯ → 群设置 → 解散群聊"
fi

# ── [5/5] 用户产出 (--nuke 分支) ─────────────────────────────
echo ""
if [ $NUKE -eq 1 ]; then
  cyan "[5/5] --nuke: 清理用户产出"
  remove workspace
  # glob 展开成多个路径, 循环 remove; 无匹配时 [ -e ] 会让 remove 里直接 return。
  for d in agents/*/workspace agents/*/core_memory.md agents/*/identity.md; do
    remove "$d"
  done
  remove team.json
else
  cyan "[5/5] 用户产出保留 (未传 --nuke)"
  note "保留: workspace/, agents/*/workspace/, agents/*/identity.md, team.json"
  note "如需彻底清到 git clone 状态, 重新运行: scripts/reset.sh --yes --nuke"
fi

# ── 汇总 ─────────────────────────────────────────────────────
echo ""
if [ $DRY_RUN -eq 1 ]; then
  green "✅ dry-run 完成。确认无误后追加 --yes 真正执行。"
else
  green "✅ reset 完成。"
  echo ""
  echo "   下一步 (docker 部署):"
  echo "     docker compose run --rm team init   # 重新创建飞书资源"
  echo "     docker compose up -d                # 启动团队"
  echo ""
  echo "   下一步 (host-native 部署):"
  echo "     python3 scripts/setup.py            # 重新创建飞书资源"
  echo "     bash scripts/start-team.sh          # 启动团队"
fi
