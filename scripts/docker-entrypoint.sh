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

# PYTHONPATH 三道注入的最后一道兜底 (Dockerfile ENV / compose env / 这里)。
# 即使外层全漏配,容器内任何 python3 -m claudeteam.* 都能直接 import。
export PYTHONPATH="${PYTHONPATH:-/app/src}"
if [ -z "${CLAUDETEAM_STATE_DIR:-}" ]; then
  if [ "$ROOT" = "/app" ]; then
    export CLAUDETEAM_STATE_DIR="/app/state"
  else
    export CLAUDETEAM_STATE_DIR="$ROOT/workspace/shared/state"
  fi
fi
if [ -z "${CLAUDETEAM_CODEX_REQUIRE_NPM_PACKAGE:-}" ] && [ "$ROOT" = "/app" ]; then
  export CLAUDETEAM_CODEX_REQUIRE_NPM_PACKAGE=1
fi

env_enabled() {
  case "$(printf '%s' "${1:-}" | tr '[:upper:]' '[:lower:]')" in
    1|true|yes|on) return 0 ;;
    *) return 1 ;;
  esac
}

echo "🐳 ClaudeTeam Docker 启动 (mode=$MODE)..."
echo "   Node.js: $(node --version)"
echo "   Python:  $(python3 --version)"
if command -v claude >/dev/null 2>&1; then
  echo "   Claude:  $(claude --version 2>/dev/null || echo 'present')"
else
  echo "   Claude:  disabled"
fi
echo "   Codex:   $(codex --version 2>/dev/null || echo 'not found')"
echo "   lark-cli package: $(npm list -g @larksuite/cli --depth=0 2>/dev/null | sed -n '2p' || echo 'not found')"
echo "   State dir: $CLAUDETEAM_STATE_DIR"
echo ""

mkdir -p "$CLAUDETEAM_STATE_DIR"
chmod 700 "$CLAUDETEAM_STATE_DIR" 2>/dev/null || true

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
import json, os, sys
cfg=json.load(open('scripts/runtime_config.json'))
required = []
def enabled(name):
    return os.environ.get(name, '').strip().lower() in ('1','true','yes','on')
if enabled('CLAUDETEAM_ENABLE_FEISHU_REMOTE'):
    required.append('chat_id')
if enabled('CLAUDETEAM_ENABLE_BITABLE_LEGACY'):
    required += ['bitable_app_token','msg_table_id','sta_table_id']
for k in required:
    if not cfg.get(k): sys.exit('missing '+k)
" 2>/tmp/rtcfg.err; then
    echo "❌ scripts/runtime_config.json 缺少关键字段: $(cat /tmp/rtcfg.err)"
    echo "   修复: 删除该文件后重新 docker compose run --rm team init"
    exit 1
  fi
fi

# ── 飞书 App 凭证 ──────────────────────────────────────────
NEEDS_FEISHU=0
if [ "$MODE" = "init" ] || \
   env_enabled "${CLAUDETEAM_ENABLE_FEISHU_REMOTE:-0}" || \
   env_enabled "${CLAUDETEAM_ENABLE_BITABLE_LEGACY:-0}"; then
  NEEDS_FEISHU=1
fi

if [ "$NEEDS_FEISHU" = "1" ]; then
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
    echo "     现有 appId: <redacted>"
    echo "     .env 里的:  <redacted>"
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
  PROFILE_NAME=$(python3 - <<'PY'
import json

profile = ""
try:
    with open("scripts/runtime_config.json") as f:
        profile = (json.load(f).get("lark_profile") or "").strip()
except Exception:
    pass
if not profile:
    try:
        with open("team.json") as f:
            profile = (json.load(f).get("session") or "").strip()
    except Exception:
        pass
print(profile or "default")
PY
)
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
  echo "✅ 已从 .env 生成容器内 lark-cli config (profile=$PROFILE_NAME, appId=<redacted>)"
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
  echo "   修复: 将项目本地 .lark-cli-credentials/local-share 只读挂载到"
  echo "         /home/claudeteam/.local/share/lark-cli"
  echo "   或者改用 .env 里的 FEISHU_APP_ID/FEISHU_APP_SECRET,切到 inline 模式。"
  exit 1
fi
else
  echo "ℹ️  Feishu live/legacy disabled; skipping lark-cli credential check."
fi

NEEDS_CLAUDE_CODE=$(python3 - <<'PY'
import json
team = json.load(open("team.json"))
agents = team.get("agents", {})
print("1" if any(v.get("cli") == "claude-code"
                 for v in agents.values()) else "0")
PY
)

if [ "$NEEDS_CLAUDE_CODE" = "1" ]; then
  # Claude Code 认证 — 仅当 team.json 显式使用 claude-code agent 时才需要。
  if ! command -v claude >/dev/null 2>&1; then
    echo "❌ team.json 显式使用 claude-code,但当前镜像未安装该 CLI。"
    echo "   修复: 构建 legacy/dev 镜像时设置 CLAUDETEAM_INSTALL_CLAUDE_CODE=1。"
    exit 1
  fi
  if [ -z "$ANTHROPIC_API_KEY" ] && [ ! -f /home/claudeteam/.claude/.credentials.json ]; then
    echo "❌ Claude Code 没有可用凭证: 既没有 ANTHROPIC_API_KEY 环境变量,"
    echo "   也没有 /home/claudeteam/.claude/.credentials.json OAuth 凭证。"
    exit 1
  fi
  if [ -z "$ANTHROPIC_API_KEY" ] && [ ! -f /home/claudeteam/.claude.json ]; then
    echo "❌ OAuth 模式缺少 /home/claudeteam/.claude.json(账户元数据文件)。"
    exit 1
  fi

  # 确保 /app 在 projects 里且 hasTrustDialogAccepted=true。
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
    print("✅ 已为 /app 写入 Claude Code 信任标记")
PY

  # 预置 Claude Code settings.local.json。
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
else
  echo "ℹ️  team.json 未使用 legacy CLI agent; skipping legacy credential/config setup."
fi

# ── CLI auto-approve 预配 ───────────��─────────────────────────
# 容器启动时预写各 CLI 的 auto-approve 配置,避免运行时弹审批对话框卡死 tmux。
# Claude Code 已通过 --dangerously-skip-permissions 处理,这里只管第三方 CLI。

# Kimi: config.toml → default_yolo = true
KIMI_CFG="/home/claudeteam/.kimi/config.toml"
if [ -f "$KIMI_CFG" ]; then
  # 已有配置(credential bind mount),修改 default_yolo
  if grep -q "default_yolo" "$KIMI_CFG"; then
    sed -i 's/default_yolo\s*=\s*false/default_yolo = true/' "$KIMI_CFG"
  else
    echo 'default_yolo = true' >> "$KIMI_CFG"
  fi
else
  mkdir -p "$(dirname "$KIMI_CFG")"
  cat > "$KIMI_CFG" <<'TOML'
default_yolo = true
TOML
fi

# Codex: config.json → approval_mode = "full-auto" (persisted)
# 命令行 --full-auto 已在 adapter 里,这里写持久化配置作为 belt-and-suspenders。
CODEX_CFG="/home/claudeteam/.codex/config.json"
mkdir -p "$(dirname "$CODEX_CFG")"
if [ ! -f "$CODEX_CFG" ] || ! python3 -c "import json; json.load(open('$CODEX_CFG'))" 2>/dev/null; then
  echo '{"approval_mode": "full-auto"}' > "$CODEX_CFG"
else
  python3 -c "
import json
p = '$CODEX_CFG'
with open(p) as f:
    d = json.load(f)
d['approval_mode'] = 'full-auto'
with open(p, 'w') as f:
    json.dump(d, f, indent=2)
"
fi

# Codex: trust the container project path up front. Without this, the first
# prod-hardened launch prompts "Do you trust /app?" and exits back to shell,
# leaving manager with no live Codex process.
CODEX_TOML="/home/claudeteam/.codex/config.toml"
mkdir -p "$(dirname "$CODEX_TOML")"
touch "$CODEX_TOML"
python3 - <<'PY'
from pathlib import Path

p = Path("/home/claudeteam/.codex/config.toml")
text = p.read_text(errors="ignore")
block = '[projects."/app"]\ntrust_level = "trusted"\n'
if '[projects."/app"]' not in text:
    if text and not text.endswith("\n"):
        text += "\n"
    if text:
        text += "\n"
    text += block
    p.write_text(text)
PY

# Gemini: settings.json → sandbox_mode = "yolo" (if supported)
# adapter spawn_cmd 已带 --approval-mode=yolo;持久化配置作为兜底。
GEMINI_CFG="/home/claudeteam/.gemini/settings.json"
mkdir -p "$(dirname "$GEMINI_CFG")"
if [ ! -f "$GEMINI_CFG" ] || ! python3 -c "import json; json.load(open('$GEMINI_CFG'))" 2>/dev/null; then
  echo '{"sandbox_mode": "yolo"}' > "$GEMINI_CFG"
else
  python3 -c "
import json
p = '$GEMINI_CFG'
with open(p) as f:
    d = json.load(f)
d['sandbox_mode'] = 'yolo'
with open(p, 'w') as f:
    json.dump(d, f, indent=2)
"
fi

# Gemini: trust 当前工作目录,避免首次 TUI trust prompt 卡住 smoke/lazy wake。
GEMINI_TRUSTED="/home/claudeteam/.gemini/trustedFolders.json"
mkdir -p "$(dirname "$GEMINI_TRUSTED")"
python3 - <<'PY'
import json
from pathlib import Path

p = Path("/home/claudeteam/.gemini/trustedFolders.json")
try:
    data = json.loads(p.read_text()) if p.exists() else {}
except Exception:
    data = {}
data["/app"] = "TRUST_FOLDER"
p.write_text(json.dumps(data, indent=2, ensure_ascii=False))
PY

echo "✅ CLI auto-approve 配置已预写 (kimi/codex/gemini)"

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

# 清理上一个容器残留的 PID 锁文件。bind-mount 的 scripts/ 跨 compose down/up
# 持久化,旧容器的 PID 可能被新容器内不相关进程复用,导致 _acquire_pid_lock
# 误判"已在运行"而 sys.exit(1)。
rm -f "$CLAUDETEAM_STATE_DIR"/*.pid 2>/dev/null || true
rm -f scripts/.*.pid 2>/dev/null || true

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
    local spawn_cmd
    spawn_cmd=$(python3 -m claudeteam.cli_adapters.resolve "$agent" spawn_cmd "${AGENT_MODELS[$agent]}")
    tmux send-keys -t "$SESSION:$agent" "$spawn_cmd" Enter
  fi
}

# 前置 preflight: Claude OAuth 凭证健康 → 才拉 worker;fail 则只拉 manager 窗口 + 红横幅。
# 理由: OAuth 过期时 worker spawn 会 401 死循环;红横幅 >> 容器反复重启。(方案 §1.3)
PREFLIGHT_FAILED=0
PREFLIGHT_MSG=""
if [ -f "$ROOT/scripts/preflight_claude_auth.sh" ]; then
  if PREFLIGHT_MSG=$(bash "$ROOT/scripts/preflight_claude_auth.sh" 2>&1); then
    echo "✅ Claude OAuth preflight: $PREFLIGHT_MSG"
  else
    echo "🚨 Claude OAuth preflight FAILED: $PREFLIGHT_MSG"
    echo "   只拉 manager 窗口 + 挂红横幅 — host 侧 \`claude /login\` 后 docker restart 恢复。"
    PREFLIGHT_FAILED=1
  fi
else
  echo "ℹ️  preflight_claude_auth.sh 不存在,跳过凭证前置检查"
fi

spawn_one "${AGENTS[0]}" --first
sleep 2

if [ "$PREFLIGHT_FAILED" = "1" ]; then
  # 在 manager pane 覆盖一个红色横幅 (ANSI 背景红)
  tmux send-keys -t "$SESSION:${AGENTS[0]}" "clear" Enter
  tmux send-keys -t "$SESSION:${AGENTS[0]}" \
    "printf '\\033[1;41;97m 🚨 Claude OAuth preflight FAILED: ${PREFLIGHT_MSG//\'/} — workers not spawned. Host: claude /login → docker restart. \\033[0m\\n'" Enter
  echo "⏭️  跳过 worker spawn (preflight 失败)"
else
  for agent in "${AGENTS[@]:1}"; do
    spawn_one "$agent"
    sleep 2
  done
fi

# Router（lark-cli WebSocket 事件流）
# 默认 local-core/no-live 容器不启动 router。live/smoke 必须显式设置
# CLAUDETEAM_ENABLE_FEISHU_REMOTE=1,并使用隔离测试群和隔离凭证。
ROUTER_STARTED=0
tmux new-window -t "$SESSION" -n "router" -c "$ROOT"
if env_enabled "${CLAUDETEAM_ENABLE_FEISHU_REMOTE:-0}"; then
  # 从 runtime_config.json 读 lark_profile，确保多 profile 共存时订阅到正确的 App。
  # 不带 --profile 会落到 lark-cli 的默认 profile，在共享宿主机 ~/.lark-cli 时
  # 极易订阅错 App 的事件流。
  LARK_PROFILE=$(python3 -c "import json; print(json.load(open('scripts/runtime_config.json')).get('lark_profile') or '')" 2>/dev/null)
  PROFILE_FLAG=""
  if [ -n "$LARK_PROFILE" ]; then
    PROFILE_FLAG="--profile $LARK_PROFILE"
  fi
  tmux send-keys -t "$SESSION:router" "npx @larksuite/cli $PROFILE_FLAG event +subscribe --event-types im.message.receive_v1 --compact --quiet --force --as bot | python3 scripts/feishu_router.py --stdin" Enter
  ROUTER_STARTED=1
else
  tmux send-keys -t "$SESSION:router" \
    "clear && echo 'router disabled: set CLAUDETEAM_ENABLE_FEISHU_REMOTE=1 only for isolated live smoke/profile'" Enter
fi

# 看板同步是 legacy Bitable adapter,默认关闭。
KANBAN_STARTED=0
tmux new-window -t "$SESSION" -n "kanban" -c "$ROOT"
if env_enabled "${CLAUDETEAM_ENABLE_BITABLE_LEGACY:-0}"; then
  tmux send-keys -t "$SESSION:kanban" "python3 scripts/kanban_sync.py daemon" Enter
  KANBAN_STARTED=1
else
  tmux send-keys -t "$SESSION:kanban" \
    "clear && echo 'kanban disabled: set CLAUDETEAM_ENABLE_BITABLE_LEGACY=1 only for explicit legacy export'" Enter
fi

# 等已启用的守护进程写出 PID 锁文件后再启动 watchdog。
if [ "$ROUTER_STARTED" = "1" ] || [ "$KANBAN_STARTED" = "1" ]; then
  echo "⏳ 等待已启用的 router / kanban 启动..."
  for i in $(seq 1 30); do
    router_ok=1
    kanban_ok=1
    [ "$ROUTER_STARTED" = "1" ] && [ ! -f "$CLAUDETEAM_STATE_DIR/router.pid" ] && router_ok=0
    [ "$KANBAN_STARTED" = "1" ] && [ ! -f "$CLAUDETEAM_STATE_DIR/kanban_sync.pid" ] && kanban_ok=0
    if [ "$router_ok" = "1" ] && [ "$kanban_ok" = "1" ]; then
      echo "   ✓ 已启用守护进程 PID 就位"
      break
    fi
    sleep 1
  done
  if [ "$ROUTER_STARTED" = "1" ] && [ ! -f "$CLAUDETEAM_STATE_DIR/router.pid" ]; then
    echo "⚠️  router 已启用但未写出 PID 文件: $CLAUDETEAM_STATE_DIR/router.pid"
  fi
  if [ "$KANBAN_STARTED" = "1" ] && [ ! -f "$CLAUDETEAM_STATE_DIR/kanban_sync.pid" ]; then
    echo "⚠️  kanban 已启用但未写出 PID 文件: $CLAUDETEAM_STATE_DIR/kanban_sync.pid"
  fi
else
  echo "ℹ️  router/kanban 默认关闭(local-core/no-live); watchdog 将不监控 live/legacy adapter。"
fi

# Watchdog
tmux new-window -t "$SESSION" -n "watchdog" -c "$ROOT"
tmux send-keys -t "$SESSION:watchdog" "python3 scripts/watchdog.py" Enter

# Claude OAuth token guard (方案 §1.3 P0): 后台守护,每 30min 扫 TTL + 快过期时发 inbox 告警。
# nohup + setsid 起,断 tty 且独立进程组,容器主循环退出不拖它;日志落 state 目录,
# 不占 tmux 窗口(guard 是"健康检测"类守护,attach 起来刷屏没价值)。
if [ -f "$ROOT/scripts/claude_token_guard.sh" ]; then
  GUARD_LOG="$CLAUDETEAM_STATE_DIR/claude_token_guard.log"
  nohup setsid bash "$ROOT/scripts/claude_token_guard.sh" \
    >>"$GUARD_LOG" 2>&1 &
  echo "🛡️  claude_token_guard: pid=$! log=$GUARD_LOG"
fi

# supervisor_ticker: 周期性触发 supervisor_tick.sh (lazy_wake_v2 §A.3 + 方案 §2.2.4)
# 用独立 tmux 窗口跑 while-sleep 循环: 比 cron 简单, 比 nohup 可见,
# attach 进来就能看到每次 tick 的输出。间隔由 CLAUDETEAM_SUPERVISOR_INTERVAL 控制
# (默认 900s = 15 分钟)。lazy-mode=off 时跳过,避免空转调用 Haiku。
#
# 方案 §2.2.4: 拉 ticker 窗口之前先同步跑一次 supervisor_tick.sh,
# 让 supervisor 第一轮就完成冷启动,不依赖 15min 的漫长等待。
# ticker 循环: 每轮 tick 后追跑 supervisor_apply.sh (决策→执行解耦)。
if [ "$LAZY_MODE" = "on" ]; then
  echo "⏰ supervisor_tick: 同步跑首轮 (冷启动 supervisor)..."
  bash "$ROOT/scripts/supervisor_tick.sh" || echo "⚠️  首轮 tick exit=$?"

  tmux new-window -t "$SESSION" -n "supervisor_ticker" -c "$ROOT"
  tmux send-keys -t "$SESSION:supervisor_ticker" \
    "while sleep \${CLAUDETEAM_SUPERVISOR_INTERVAL:-900}; do echo \"[\$(date '+%F %T')] ⏰ tick start\"; bash scripts/supervisor_tick.sh || echo \"[\$(date '+%F %T')] ⚠️  tick exit=\$?\"; bash scripts/supervisor_apply.sh || echo \"[\$(date '+%F %T')] ⚠️  apply exit=\$?\"; done" \
    Enter
fi

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

if ! probe_agents 15; then
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

    # thinking init hint (F2: per-agent thinking level)
    THINKING_HINT=$(python3 -m claudeteam.cli_adapters.resolve "$agent" thinking_init_hint \
      "$(python3 -m claudeteam.runtime.config resolve-thinking "$agent" 2>/dev/null)" 2>/dev/null) && \
      INIT_MSG="${INIT_MSG}

【Thinking 指引】${THINKING_HINT}"

    INIT_MSG="$INIT_MSG" python3 - "$SESSION" "$agent" <<'PY'
import os, sys
sys.path.insert(0, os.path.join(os.getcwd(), "src"))
from claudeteam.runtime.tmux_utils import inject_when_idle

session, agent = sys.argv[1], sys.argv[2]
ok = inject_when_idle(session, agent, os.environ["INIT_MSG"],
                      wait_secs=20, force_after_wait=False)
raise SystemExit(0 if ok else 1)
PY
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
