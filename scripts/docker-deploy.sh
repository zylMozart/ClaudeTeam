#!/bin/bash
# ClaudeTeam 一键 Docker 部署脚本 (宿主机侧)
#
# 用途: 把 README 里散落在 Quick Start + Phase 1/2 的手动步骤集中到一个脚本,
#       让用户在凭证就绪后能用一条命令完成"检查 + setup.py + docker compose up"。
#
# 不做的事:
#   - 不配置 lark-cli (需要浏览器扫码,必须手动完成一次)
#   - 不登录 Claude Code (同上)
#   - 不创建 team.json (团队结构设计是交互式,由用户决定)
#
# 做的事:
#   - 检查所有前置条件,任何缺失立即报错
#   - 如果 scripts/runtime_config.json 缺失,在宿主机跑一次 setup.py
#   - docker compose build + up -d
#   - 等容器 healthy, 打印关键状态

set -e
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
cd "$ROOT"

red()   { echo -e "\033[31m$*\033[0m"; }
green() { echo -e "\033[32m$*\033[0m"; }
yellow(){ echo -e "\033[33m$*\033[0m"; }

need() {
  if ! command -v "$1" >/dev/null 2>&1; then
    red "❌ 缺少命令: $1"
    exit 1
  fi
}

echo "🔍 检查前置条件..."
need docker
need python3
need node

# team.json 必须由用户或 Claude Code 交互创建,这里只检查存在性
if [ ! -f team.json ]; then
  red "❌ team.json 不存在。"
  echo "   建议: 先在宿主机跑一次 \`claude\` 打开本项目,按 README Phase 2 的"
  echo "         引导创建团队;或者手写一份最小 team.json:"
  echo '         {"session":"myteam","agents":{"manager":{"role":"主管","emoji":"🎯","color":"blue"}}}'
  exit 1
fi

# lark-cli 凭证必须在宿主机预先配好
if [ ! -f "$HOME/.lark-cli/config.json" ]; then
  red "❌ lark-cli 未配置。"
  echo "   请先在宿主机跑: npx @larksuite/cli config init --new"
  echo "   按 README Phase 1 完成扫码 + 发布。"
  exit 1
fi
if [ ! -f "$HOME/.local/share/lark-cli/master.key" ]; then
  red "❌ ~/.local/share/lark-cli 不存在或不完整。lark-cli 加密 secret 存储缺失。"
  echo "   请重新运行 lark-cli config init 或 config init --new。"
  exit 1
fi

# Claude Code 认证二选一
if [ -z "$ANTHROPIC_API_KEY" ] && [ ! -f "$HOME/.claude/.credentials.json" ]; then
  red "❌ Claude Code 没有可用凭证。"
  echo "   二选一:"
  echo "     (a) export ANTHROPIC_API_KEY=sk-... 再重跑本脚本"
  echo "     (b) 在宿主机跑 \`claude\` 完成一次登录,生成 ~/.claude/.credentials.json"
  exit 1
fi
if [ -z "$ANTHROPIC_API_KEY" ] && [ ! -f "$HOME/.claude.json" ]; then
  red "❌ OAuth 模式下缺少 ~/.claude.json(Claude Code 账户元数据)。"
  echo "   请先在宿主机跑一次 \`claude\` 让它生成这个文件。"
  exit 1
fi

green "✓ 所有前置条件满足"
echo ""

# runtime_config.json — 缺失就在宿主机跑 setup.py
if [ ! -f scripts/runtime_config.json ]; then
  yellow "⚠️  scripts/runtime_config.json 不存在,准备在宿主机运行 setup.py..."
  echo "   (如果飞书 App 权限未发布,这一步会报 no permission)"
  echo ""
  # 尊重多团队冲突检测,但自动 accept 如果用户没显式拒绝
  CLAUDE_TEAM_ACCEPT_SHARED_PROFILE="${CLAUDE_TEAM_ACCEPT_SHARED_PROFILE:-1}" \
    python3 scripts/setup.py
  if [ ! -f scripts/runtime_config.json ]; then
    red "❌ setup.py 未生成 runtime_config.json,中止部署。"
    exit 1
  fi
  green "✓ setup.py 完成"
  echo ""
fi

# Docker build + up
echo "🐳 构建镜像..."
docker compose build 2>&1 | tail -5
echo ""
echo "🚀 启动容器..."
docker compose up -d
echo ""

# 等 healthcheck 变绿
CONTAINER=claudeteam
echo "⏳ 等待容器 healthy (最多 120s)..."
for i in $(seq 1 24); do
  STATUS=$(docker inspect "$CONTAINER" --format '{{.State.Health.Status}}' 2>/dev/null || echo "unknown")
  if [ "$STATUS" = "healthy" ]; then
    green "✓ 容器 healthy"
    break
  fi
  sleep 5
done

# 打印 tmux 状态快照
echo ""
echo "📊 tmux 窗口状态:"
docker exec "$CONTAINER" tmux list-windows -t "$(python3 -c 'import json; print(json.load(open("team.json"))["session"])')" 2>&1 | sed 's/^/   /'

# 打印飞书群邀请链接
LINK=$(python3 -c "import json; print(json.load(open('scripts/runtime_config.json')).get('share_link',''))" 2>/dev/null)
if [ -n "$LINK" ]; then
  echo ""
  green "📎 飞书群聊邀请链接(发给需要和团队对话的人):"
  echo "   $LINK"
fi

echo ""
green "✅ 部署完成。常用命令:"
echo "   docker compose logs -f        # 查看日志"
echo "   docker exec -it $CONTAINER tmux attach -t \$(python3 -c 'import json; print(json.load(open(\"team.json\"))[\"session\"])')"
echo "   docker compose down           # 停止"
