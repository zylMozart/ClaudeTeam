#!/bin/bash
# ClaudeTeam Docker 入口脚本
# 职责：检查配置 → 启动团队 → 保持容器前台运行
set -e

ROOT="$(cd "$(dirname "$0")/.." && pwd)"
cd "$ROOT"

echo "🐳 ClaudeTeam Docker 启动..."
echo "   Node.js: $(node --version)"
echo "   Python:  $(python3 --version)"
echo "   Claude:  $(claude --version 2>/dev/null || echo 'not found')"
echo "   lark-cli: $(npx @larksuite/cli --version 2>/dev/null || echo 'not found')"
echo ""

# ── 前置检查 ──────────────────────────────────────────────────

# 常见坑: docker-compose.yml 里的 bind mount 目标文件如果宿主机上不存在,
# Docker 会自动创建一个同名"目录"顶替,后续 cat/python 会报"Is a directory"
# 而不是清晰的"文件缺失"。这个 helper 一次性处理三种失败: 是目录/为空/
# 不是合法 JSON,给出统一的报错格式。
require_json_file() {
  local path="$1" remediation="$2"
  if [ -d "$path" ]; then
    echo "❌ $path 是一个目录,不是文件(bind mount 目标在宿主机不存在)。"
    [ -n "$remediation" ] && echo "   $remediation"
    exit 1
  fi
  if [ ! -s "$path" ]; then
    echo "❌ $path 缺失或为空。"
    [ -n "$remediation" ] && echo "   $remediation"
    exit 1
  fi
  if ! python3 -c "import json,sys; json.load(open('$path'))" 2>/dev/null; then
    echo "❌ $path 不是合法 JSON。"
    [ -n "$remediation" ] && echo "   $remediation"
    exit 1
  fi
}

require_json_file team.json \
  "在宿主机上手写一份 team.json 后重新 docker compose up。"

require_json_file scripts/runtime_config.json \
  "在宿主机上先跑 python3 scripts/setup.py 生成该文件。"

if ! python3 -c "
import json,sys
cfg=json.load(open('scripts/runtime_config.json'))
for k in ('bitable_app_token','msg_table_id','sta_table_id','chat_id'):
    if not cfg.get(k): sys.exit('missing '+k)
" 2>/tmp/rtcfg.err; then
  echo "❌ scripts/runtime_config.json 缺少关键字段: $(cat /tmp/rtcfg.err)"
  echo "   修复: 删除该文件后在宿主机上重新跑 python3 scripts/setup.py"
  exit 1
fi

# ── 飞书 App 凭证 ──────────────────────────────────────────
# 两条路径:
#   (a) 环境变量 FEISHU_APP_ID + FEISHU_APP_SECRET 都给了 → 生成 inline 格式
#       config.json,不依赖 ~/.local/share/lark-cli/ 加密存储。优先级最高,
#       存在即覆盖。适合新用户直接从 .env 起步。
#   (b) 都没给 → 期待宿主机挂载已经配好的 ~/.lark-cli。适合已经跑过
#       lark-cli config init --new 的老用户。
if [ -n "$FEISHU_APP_ID" ] && [ -n "$FEISHU_APP_SECRET" ]; then
  mkdir -p /home/claudeteam/.lark-cli
  python3 - <<'PY'
import json, os
cfg = {"apps": [{
    "appId":     os.environ["FEISHU_APP_ID"],
    "appSecret": os.environ["FEISHU_APP_SECRET"],
    "brand":     os.environ.get("FEISHU_BRAND", "feishu") or "feishu",
    "lang":      "zh",
    "users":     [],
}]}
with open("/home/claudeteam/.lark-cli/config.json", "w") as f:
    json.dump(cfg, f, indent=2)
os.chmod("/home/claudeteam/.lark-cli/config.json", 0o600)
PY
  echo "✅ 已从环境变量生成 /home/claudeteam/.lark-cli/config.json (appId=$FEISHU_APP_ID, brand=${FEISHU_BRAND:-feishu})"
fi

if [ ! -f /home/claudeteam/.lark-cli/config.json ]; then
  echo "❌ lark-cli 未配置: /home/claudeteam/.lark-cli/config.json 不存在。"
  echo "   二选一:"
  echo "     (a) 在 .env 里填 FEISHU_APP_ID 和 FEISHU_APP_SECRET,由容器自动生成"
  echo "     (b) 在宿主机跑 \`npx @larksuite/cli config init --new\` 扫码,"
  echo "         确保 ~/.lark-cli 和 ~/.local/share/lark-cli 都存在"
  exit 1
fi

# 解析 config.json 判断 secret 的存储形式。如果是 {"source":"keychain",...}
# 的引用形式,必须依赖 ~/.local/share/lark-cli/master.key + 对应的 .enc 文件
# 才能拿到明文;如果是纯字符串(inline 模式),就不需要加密存储。
SECRET_MODE=$(python3 -c "
import json
cfg = json.load(open('/home/claudeteam/.lark-cli/config.json'))
app = cfg.get('apps', [{}])[0]
sec = app.get('appSecret')
print('keychain' if isinstance(sec, dict) else 'inline')
" 2>/dev/null || echo "unknown")

if [ "$SECRET_MODE" = "keychain" ] && [ ! -f /home/claudeteam/.local/share/lark-cli/master.key ]; then
  echo "❌ lark-cli config.json 使用 keychain 引用模式但加密存储未挂载"
  echo "   (~/.local/share/lark-cli/master.key 缺失)"
  echo "   修复: 在 docker-compose.yml 里加一行"
  echo "         - ~/.local/share/lark-cli:/home/claudeteam/.local/share/lark-cli"
  echo "   或者改用 .env 里的 FEISHU_APP_ID/FEISHU_APP_SECRET,切到 inline 模式。"
  exit 1
fi

# Claude Code 认证 — 两选一
if [ -z "$ANTHROPIC_API_KEY" ] && [ ! -f /home/claudeteam/.claude/.credentials.json ]; then
  echo "❌ Claude Code 没有可用凭证: 既没有 ANTHROPIC_API_KEY 环境变量,"
  echo "   也没有 /home/claudeteam/.claude/.credentials.json OAuth 凭证。"
  echo "   二选一:"
  echo "     (a) export ANTHROPIC_API_KEY=sk-... 再 docker compose up"
  echo "     (b) 宿主机先 \`claude login\` 生成 ~/.claude/.credentials.json"
  exit 1
fi
# OAuth 模式下还需要 ~/.claude.json(账户元数据,和 .credentials.json 分开存)
if [ -z "$ANTHROPIC_API_KEY" ] && [ ! -f /home/claudeteam/.claude.json ]; then
  echo "❌ OAuth 模式缺少 /home/claudeteam/.claude.json(账户元数据文件)。"
  echo "   Claude Code 会弹出 'Select login method' 菜单阻塞启动。"
  echo "   修复: docker-compose.yml 增加一行"
  echo "         - ~/.claude.json:/home/claudeteam/.claude.json"
  exit 1
fi

# 确保 /app 在 projects 里且 hasTrustDialogAccepted=true,否则 Claude Code
# 首次进入 /app 会弹出 "Is this a project you trust?" 交互菜单,和主题菜单一样
# 会吃掉 tmux send-keys 的初始化消息。这里做一次幂等写入。
python3 - <<'PY'
import json, os
p = "/home/claudeteam/.claude.json"
if not os.path.exists(p):
    raise SystemExit(0)
with open(p) as f:
    d = json.load(f)
projects = d.setdefault("projects", {})
app = projects.setdefault("/app", {})
if not app.get("hasTrustDialogAccepted"):
    app["hasTrustDialogAccepted"] = True
    app.setdefault("hasCompletedProjectOnboarding", True)
    app.setdefault("projectOnboardingSeenCount", 1)
    with open(p, "w") as f:
        json.dump(d, f, indent=2)
    print("✅ 已为 /app 写入信任标记")
PY

# ── 启动团队（非交互模式）────────────────────────────────────

# start-team.sh 最后会 tmux attach，容器内不需要 attach
# 改为启动后保持前台运行

SESSION=$(python3 -c "import json; print(json.load(open('team.json'))['session'])")
AGENTS=($(python3 -c "import json; print(' '.join(json.load(open('team.json'))['agents'].keys()))"))

# 如果 session 已存在（容器重启场景），先清理
if tmux has-session -t "$SESSION" 2>/dev/null; then
  echo "⚠️  清理旧 session: $SESSION"
  tmux kill-session -t "$SESSION"
fi

echo "🚀 启动 Agent 团队..."
echo "   tmux session: $SESSION"
echo "   Agents: ${AGENTS[*]}"

# 创建 tmux session + agent 窗口
tmux new-session -d -s "$SESSION" -n "${AGENTS[0]}" -c "$ROOT"
tmux send-keys -t "$SESSION:${AGENTS[0]}" "IS_SANDBOX=1 claude --dangerously-skip-permissions --name ${AGENTS[0]}" Enter
sleep 2

for agent in "${AGENTS[@]:1}"; do
  tmux new-window -t "$SESSION" -n "$agent" -c "$ROOT"
  tmux send-keys -t "$SESSION:$agent" "IS_SANDBOX=1 claude --dangerously-skip-permissions --name $agent" Enter
  sleep 2
done

# Router（lark-cli WebSocket 事件流）
tmux new-window -t "$SESSION" -n "router" -c "$ROOT"
tmux send-keys -t "$SESSION:router" "npx @larksuite/cli event +subscribe --event-types im.message.receive_v1 --compact --quiet --force --as bot | python3 scripts/feishu_router.py --stdin" Enter

# 看板同步
tmux new-window -t "$SESSION" -n "kanban" -c "$ROOT"
tmux send-keys -t "$SESSION:kanban" "python3 scripts/kanban_sync.py daemon" Enter

# 等 router / kanban 真正起来并写出各自的 PID 锁文件后再启动 watchdog。
# 不等的话 watchdog 的首次检查会在 t=0 即认定目标未启动 → 错误地"重启"它们,
# 后果是 router 被启动两遍,两个 lark-cli WebSocket 订阅同时跑,事件收两次。
echo "⏳ 等待 router / kanban 启动..."
for i in $(seq 1 30); do
  if [ -f scripts/.router.pid ] && [ -f scripts/.kanban_sync.pid ]; then
    echo "   ✓ router + kanban PID 就位"
    break
  fi
  sleep 1
done
if [ ! -f scripts/.router.pid ] || [ ! -f scripts/.kanban_sync.pid ]; then
  echo "⚠️  等待 30s 后 router 或 kanban 仍未写出 PID 文件,继续启动 watchdog。"
  echo "   这可能意味着 router/kanban 启动失败,请 docker exec 进入 tmux 查看。"
fi

# Watchdog
tmux new-window -t "$SESSION" -n "watchdog" -c "$ROOT"
tmux send-keys -t "$SESSION:watchdog" "python3 scripts/watchdog.py" Enter

sleep 2

# 发送初始化消息
for agent in "${AGENTS[@]}"; do
  INIT_MSG="你是团队的 ${agent}。

【必读】请读取：agents/${agent}/identity.md — 了解你的角色和通讯规范
【然后立即执行】
1. python3 scripts/feishu_msg.py inbox ${agent}
2. python3 scripts/feishu_msg.py status ${agent} 进行中 \"初始化完成，待命中\"

准备好后，简短汇报：你是谁、当前状态、有无未读消息。"

  tmux send-keys -t "$SESSION:$agent" "$INIT_MSG" Enter
  sleep 1
done

echo ""
echo "✅ 团队已在容器内启动！"
echo "   进入 tmux: docker exec -it <container> tmux attach -t $SESSION"
echo ""

# ── 保持容器前台运行 ──────────────────────────────────────────
# 监听 tmux session，session 结束则容器退出
while tmux has-session -t "$SESSION" 2>/dev/null; do
  sleep 30
done

echo "⚠️  tmux session 已结束，容器退出。"
