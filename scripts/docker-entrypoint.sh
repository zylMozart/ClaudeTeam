#!/bin/bash
# ClaudeTeam Docker 入口脚本
#
# 支持两种运行模式（通过第一个参数区分）：
#   init   — 一次性：生成 lark-cli config → 跑 setup.py → 退出
#            用法: docker compose run --rm team init
#            首次部署时跑这个,它会创建飞书 Bitable + 群聊 + runtime_config.json
#   start  — 默认：跑完前置检查后启动团队 + router + watchdog,保持前台运行
#            用法: docker compose up -d
set -e

MODE="${1:-start}"
if [ "$MODE" != "init" ] && [ "$MODE" != "start" ]; then
  echo "❌ 未知模式: $MODE"
  echo "   用法: docker-entrypoint.sh [init|start]"
  exit 2
fi

ROOT="$(cd "$(dirname "$0")/.." && pwd)"
cd "$ROOT"

echo "🐳 ClaudeTeam Docker 启动 (mode=$MODE)..."
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

# init 模式下 runtime_config.json 由本次 setup.py 创建,允许它现在为空/不存在。
# start 模式下必须已经有一份合法的 runtime_config.json。
if [ "$MODE" = "start" ]; then
  require_json_file scripts/runtime_config.json \
    "首次部署请跑: docker compose run --rm team init"

  if ! python3 -c "
import json,sys
cfg=json.load(open('scripts/runtime_config.json'))
for k in ('bitable_app_token','msg_table_id','sta_table_id','chat_id'):
    if not cfg.get(k): sys.exit('missing '+k)
" 2>/tmp/rtcfg.err; then
    echo "❌ scripts/runtime_config.json 缺少关键字段: $(cat /tmp/rtcfg.err)"
    echo "   修复: 删除该文件后重新 docker compose run --rm team init"
    exit 1
  fi
fi

# ── 飞书 App 凭证 ──────────────────────────────────────────
# 两条路径:
#   (a) .env 里填了 FEISHU_APP_ID + FEISHU_APP_SECRET → 生成 inline 格式的
#       容器本地 config.json,不依赖宿主机。这是推荐方式。
#   (b) 都没填 → 期待宿主机通过 bind mount 把已经配好的 ~/.lark-cli 和
#       ~/.local/share/lark-cli 挂进容器(compose 里默认注释掉,需要手动
#       取消注释)。适合已经有 lark-cli 环境的老用户。
#
# inline 模式里写入的 profile 名字取自 team.json 的 session 字段 (如 "test3"),
# 这样后续 `--profile <session>` 在宿主机 start-team.sh 和容器内 docker-entrypoint
# 两边都能一致工作。
if [ -n "$FEISHU_APP_ID" ] && [ -n "$FEISHU_APP_SECRET" ]; then
  # 安全检查: 如果 /home/claudeteam/.lark-cli/config.json 已经存在且里面的
  # appId 不是我们要写入的这个(= 是从宿主机 bind mount 进来的旧 config),
  # 直接覆盖会静默污染宿主机状态。拒绝工作,让用户显式决定。
  EXISTING_APP_ID=""
  if [ -f /home/claudeteam/.lark-cli/config.json ]; then
    EXISTING_APP_ID=$(python3 -c "
import json, sys
try:
    cfg = json.load(open('/home/claudeteam/.lark-cli/config.json'))
    apps = cfg.get('apps', [])
    if apps:
        print(apps[0].get('appId', ''))
except Exception:
    pass
" 2>/dev/null)
  fi
  if [ -n "$EXISTING_APP_ID" ] && [ "$EXISTING_APP_ID" != "$FEISHU_APP_ID" ]; then
    echo "❌ 检测到 /home/claudeteam/.lark-cli/config.json 已指向另一个 App:"
    echo "     现有 appId: $EXISTING_APP_ID"
    echo "     .env 里的:  $FEISHU_APP_ID"
    echo ""
    echo "   可能的原因:"
    echo "     1) docker-compose.yml 里的 ~/.lark-cli bind mount 被取消注释,"
    echo "        容器正在看到宿主机的 lark-cli 配置。"
    echo "     2) 之前的容器实例通过 named volume / docker exec 写入了 config.json"
    echo "        并残留在当前 volume 里。"
    echo ""
    echo "   请二选一:"
    echo "     (a) 若为情况 1: 重新注释掉 docker-compose.yml 里的 ~/.lark-cli mount"
    echo "     (b) 若为情况 2 且确定可覆盖: docker compose down -v && docker compose up"
    echo "     (c) 或者清空 .env 里的 FEISHU_APP_ID/SECRET, 走 bind mount 路径"
    exit 1
  fi

  mkdir -p /home/claudeteam/.lark-cli
  PROFILE_NAME=$(python3 -c "import json; print(json.load(open('team.json')).get('session','default'))" 2>/dev/null)
  python3 - <<PY
import json, os
cfg = {"apps": [{
    "name":      "${PROFILE_NAME}",
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
  echo "✅ 已从 .env 生成容器内 lark-cli config (profile=$PROFILE_NAME, appId=$FEISHU_APP_ID)"
fi

if [ ! -f /home/claudeteam/.lark-cli/config.json ]; then
  echo "❌ lark-cli 未配置: /home/claudeteam/.lark-cli/config.json 不存在。"
  echo "   二选一:"
  echo "     (a) 在 .env 里填 FEISHU_APP_ID 和 FEISHU_APP_SECRET (推荐),由容器自动生成"
  echo "     (b) 在 docker-compose.yml 取消注释 ~/.lark-cli bind mount,并确保宿主机"
  echo "         跑过 \`npx @larksuite/cli config init --new\`"
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

# 预置 settings.local.json —— 绕过 "sensitive file" 权限弹窗。
# 背景 (Bug 12): 即使启动参数带了 --dangerously-skip-permissions,Claude Code
# 仍会对自己状态目录下的路径(~/.claude/projects/<escaped>/** 等) 执行硬编码的
# "敏感文件" 检查。agent 只要跑 `mkdir -p ~/.claude/projects/-app/memory` 这种
# auto-memory 初始化命令,就会被拦下弹确认框,tmux 窗口直接卡死 ——
# 没人按方向键+Enter,manager 就永远派不下去任务,群里消息表现为"无人响应"。
#
# 修法是给容器写一份 settings.local.json,permissions.allow 里加一条
# 全量 Bash 白名单 (`Bash(*)`) + 所有 Edit/Write/Read 白名单,这样 Claude
# 在检查阶段就认定"用户已经永久允许",直接跳过弹窗。
#
# 文件不入镜像、仅容器运行时生成 —— 宿主机 ~/.claude/settings.local.json
# 不受影响(我们只 bind mount 了 .credentials.json 和 .claude.json 两个顶层
# 文件,settings.local.json 落在容器自己的 rootfs 层)。
mkdir -p /home/claudeteam/.claude
python3 - <<'PY'
import json, os
p = "/home/claudeteam/.claude/settings.local.json"
existing = {}
if os.path.exists(p):
    try:
        with open(p) as f:
            existing = json.load(f)
    except Exception:
        existing = {}
perms = existing.setdefault("permissions", {})
allow = perms.setdefault("allow", [])
# 幂等: 已有就不重复加
wanted = [
    "Bash(*)",
    "Write(/home/claudeteam/.claude/**)",
    "Edit(/home/claudeteam/.claude/**)",
    "Read(/home/claudeteam/.claude/**)",
    "Write(/app/**)",
    "Edit(/app/**)",
    "Read(/app/**)",
]
for rule in wanted:
    if rule not in allow:
        allow.append(rule)
with open(p, "w") as f:
    json.dump(existing, f, indent=2)
print(f"✅ 已写入 {p} (permissions.allow={len(allow)} 条)")
PY

# ── init 模式：跑 setup.py 创建飞书资源,然后退出 ────────────
if [ "$MODE" = "init" ]; then
  echo ""
  echo "🏗  init 模式: 创建飞书 Bitable / 群聊 / 工作空间表..."

  # 把 inline 模式写的 profile 名字透给 setup.py,避免它自己去猜默认 profile。
  # bind-mount 模式(没填 FEISHU_APP_ID)下宿主机 profile 名不等于 session,
  # 不要覆盖,让 setup.py 通过 `lark-cli config show` 自行探测。
  if [ -n "$FEISHU_APP_ID" ] && [ -n "$FEISHU_APP_SECRET" ]; then
    PROFILE_NAME=$(python3 -c "import json; print(json.load(open('team.json')).get('session','default'))")
    export LARK_CLI_PROFILE="$PROFILE_NAME"
  fi

  python3 scripts/setup.py
  RC=$?
  if [ "$RC" -ne 0 ]; then
    echo "❌ setup.py 失败 (exit=$RC)"
    exit $RC
  fi

  echo ""
  echo "✅ init 完成。接下来启动团队: docker compose up -d"
  exit 0
fi

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

# lazy-mode 与白名单决策共享 lib (lazy_wake_v2 §A.2)。宿主 start-team.sh 走同一份。
# 容器场景默认 on,与宿主默认对齐;可被 docker run -e CLAUDETEAM_LAZY_MODE=off 覆盖。
LAZY_MODE="${CLAUDETEAM_LAZY_MODE:-on}"
case "$LAZY_MODE" in
  on|off) ;;
  *) echo "❌ CLAUDETEAM_LAZY_MODE 取值非法: '$LAZY_MODE' (期望 on 或 off)"; exit 2 ;;
esac
source "$ROOT/scripts/lib/tmux_team_bringup.sh"

# ── per-role 模型预解析 (lazy_wake_v2 §B) ────────────────────
# 走和 start-team.sh 完全同一份 helper。非法 model / 解析失败时 loud-fail:
# 容器入口这里直接 exit 1 —— init 消息阶段的 HALT_INIT 不适用于模型解析,
# 因为非法 model 是配置错而不是 Claude UI 启动错,restart 循环救不了。
if ! resolve_all_agent_models; then
  echo "   中止容器启动 (失败的 agent: ${FAILED_MODEL_AGENT})"
  exit 1
fi

echo "🚀 启动 Agent 团队..."
echo "   tmux session: $SESSION"
echo "   Agents: ${AGENTS[*]}"
echo "   lazy-mode: $LAZY_MODE"
print_agent_models_table

# spawn_one <agent> [--first]
#  --first → 用 new-session,否则 new-window
#  lazy-mode on 且非白名单 → pane 留 bash + 💤 banner,等 router 唤醒
#  其它情况 → 老路径直接拉 claude,带 --model (reviewer H 阻塞修复)
spawn_one() {
  local agent="$1" first="${2:-}"
  if [ "$first" = "--first" ]; then
    tmux new-session -d -s "$SESSION" -n "$agent" -c "$ROOT"
  else
    tmux new-window -t "$SESSION" -n "$agent" -c "$ROOT"
  fi
  if should_skip_agent_in_lazy_mode "$agent"; then
    tmux send-keys -t "$SESSION:$agent" \
      "clear && echo '💤 待 wake  (agent=$agent, model=${AGENT_MODELS[$agent]}, lazy-mode)' && echo '   router 收到业务消息后会唤醒本窗口'" Enter
  else
    tmux send-keys -t "$SESSION:$agent" \
      "IS_SANDBOX=1 claude --dangerously-skip-permissions --model ${AGENT_MODELS[$agent]} --name $agent" Enter
  fi
}

spawn_one "${AGENTS[0]}" --first
sleep 2
for agent in "${AGENTS[@]:1}"; do
  spawn_one "$agent"
  sleep 2
done

# Router（lark-cli WebSocket 事件流）
# 从 runtime_config.json 读 lark_profile，确保多 profile 共存时订阅到正确的 App。
# 不带 --profile 会落到 lark-cli 的默认 profile，在共享宿主机 ~/.lark-cli 时
# 极易订阅错 App 的事件流。
LARK_PROFILE=$(python3 -c "import json; print(json.load(open('scripts/runtime_config.json')).get('lark_profile') or '')" 2>/dev/null)
PROFILE_FLAG=""
if [ -n "$LARK_PROFILE" ]; then
  PROFILE_FLAG="--profile $LARK_PROFILE"
fi
tmux new-window -t "$SESSION" -n "router" -c "$ROOT"
tmux send-keys -t "$SESSION:router" "npx @larksuite/cli $PROFILE_FLAG event +subscribe --event-types im.message.receive_v1 --compact --quiet --force --as bot | python3 scripts/feishu_router.py --stdin" Enter

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

# ── Claude UI 启动探测 (Bug 11 防御) ─────────────────────────
# 共享库 scripts/lib/tmux_team_bringup.sh 已在文件上方 source。
#
# 关键差异: 容器版 probe 失败时 *不* exit 1。
# restart: unless-stopped 会把失败的容器无限快速重启,每次都把 tmux session
# 干掉,诊断输出被新实例刷掉,人根本来不及 docker exec 进来看。
# 正确做法: 设 HALT_INIT=1 跳过 init 消息循环,保留 tmux session,
# 让下面的 "while tmux has-session" 主循环把容器存活着,方便
# `docker exec -it <container> tmux attach -t $SESSION` 查原始错误输出。

# lazy-mode 下只 probe 真跑了 claude 的 agent,占位窗口跳过 (与 start-team.sh 对齐)。
ACTIVE_AGENTS=()
for a in "${AGENTS[@]}"; do
  should_skip_agent_in_lazy_mode "$a" || ACTIVE_AGENTS+=("$a")
done
export PROBE_AGENTS="${ACTIVE_AGENTS[*]}"

if ! probe_claude_agents 15; then
  diagnose_failed_agents
  echo ""
  echo "⚠️  Claude UI 启动失败。保留 tmux session 便于诊断:"
  echo "     docker exec -it <container> tmux attach -t $SESSION"
  echo ""
  echo "   修复启动问题后,重启容器: docker compose restart team"
  HALT_INIT=1
fi

sleep 2

# 发送初始化消息(仅在 probe 通过时)
# lazy-mode 下占位窗口里只有 bash,发 init 会被当 shell 命令跑 (Bug 11),只发给 ACTIVE_AGENTS。
if [ "${HALT_INIT:-0}" != "1" ]; then
  for agent in "${ACTIVE_AGENTS[@]}"; do
    INIT_MSG="你是团队的 ${agent}。

【必读】请读取：agents/${agent}/identity.md — 了解你的角色和通讯规范
【然后立即执行】
1. python3 scripts/feishu_msg.py inbox ${agent}
2. python3 scripts/feishu_msg.py status ${agent} 进行中 \"初始化完成，待命中\"

准备好后，简短汇报：你是谁、当前状态、有无未读消息。"

    tmux send-keys -t "$SESSION:$agent" "$INIT_MSG" Enter
    # Bug 15 防御: feishu_msg.py status → Bitable record-batch-create 并发写
    # 会撞限流,错峰 2.5s 与 start-team.sh 对齐。lazy-mode 下 ACTIVE_AGENTS
    # 通常只有 manager+supervisor,2.5s 对启动总耗时的影响可忽略。
    sleep 2.5
  done

  echo ""
  echo "✅ 团队已在容器内启动！"
  echo "   进入 tmux: docker exec -it <container> tmux attach -t $SESSION"
  echo ""
else
  echo ""
  echo "⚠️  已跳过 init 消息循环(HALT_INIT=1),容器将保持运行以便诊断。"
  echo ""
fi

# ── 保持容器前台运行 ──────────────────────────────────────────
# 监听 tmux session，session 结束则容器退出
while tmux has-session -t "$SESSION" 2>/dev/null; do
  sleep 30
done

echo "⚠️  tmux session 已结束，容器退出。"
