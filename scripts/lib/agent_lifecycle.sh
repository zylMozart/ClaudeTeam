#!/usr/bin/env bash
# scripts/lib/agent_lifecycle.sh — agent 生命周期管理 (lazy-wake v2)
#
# 三个函数: spawn_agent / suspend_agent / wake_agent
# 由 supervisor_tick.sh / feishu_router.py 共享,也支持 CLI 直接调用:
#   bash scripts/lib/agent_lifecycle.sh {spawn|suspend|wake} <agent>
#
# 设计要点 (来自 lazy_wake_v2 ADR):
#   - suspend 严格顺序: 状态表→保存 session_id→kill claude pid→tmux 窗口留💤
#     顺序错了 router 会把消息路给"看似活着"的 agent,丢消息。
#   - 模型解析全部走 claudeteam.runtime.config::resolve_model_for_agent (单事实源)。
#   - session_id 通过扫描 ~/.claude/projects/-app/*.jsonl 的 customTitle 字段
#     发现最新会话,持久化到 scripts/.agent_sessions.json。
#   - 函数失败用非零退出码报告,不 exit (库代码不替调用方做退出决策)。

# 路径常量 — 用 BASH_SOURCE 推导,避免 cwd 漂移
_LC_SCRIPTS_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
_LC_PROJECT_ROOT="$(cd "$_LC_SCRIPTS_DIR/.." && pwd)"
LIFECYCLE_SESSIONS_FILE="${LIFECYCLE_SESSIONS_FILE:-$_LC_SCRIPTS_DIR/.agent_sessions.json}"

# 解析 tmux session 名 (从 team.json,失败则 fallback 到 ClaudeTeam)
_lifecycle_tmux_session() {
  python3 -c "
import json, sys
try:
    print(json.load(open('$_LC_PROJECT_ROOT/team.json'))['session'])
except Exception:
    print('ClaudeTeam')
"
}

# 写 session_id (atomic via os.replace)
_lifecycle_save_session() {
  local agent="$1"
  local sid="$2"
  AGENT="$agent" SID="$sid" SESSIONS_FILE="$LIFECYCLE_SESSIONS_FILE" python3 - <<'PY'
import json, os, sys
f = os.environ["SESSIONS_FILE"]
data = {}
if os.path.exists(f):
    try:
        with open(f) as fp:
            data = json.load(fp)
    except Exception:
        data = {}
data[os.environ["AGENT"]] = os.environ["SID"]
tmp = f + ".tmp"
with open(tmp, "w") as fp:
    json.dump(data, fp, indent=2, ensure_ascii=False)
os.replace(tmp, f)
PY
}

# 读 session_id (找不到打空字符串)
_lifecycle_get_session() {
  local agent="$1"
  AGENT="$agent" SESSIONS_FILE="$LIFECYCLE_SESSIONS_FILE" python3 - <<'PY' 2>/dev/null
import json, os
f = os.environ["SESSIONS_FILE"]
if not os.path.exists(f):
    raise SystemExit(0)
try:
    with open(f) as fp:
        data = json.load(fp)
except Exception:
    raise SystemExit(0)
print(data.get(os.environ["AGENT"], ""))
PY
}

# 扫 ~/.claude/projects/-app/*.jsonl 找 customTitle == agent 的最新 sessionId
_lifecycle_discover_session() {
  local agent="$1"
  AGENT="$agent" python3 - <<'PY' 2>/dev/null
import json, os, glob
proj = os.path.expanduser("~/.claude/projects/-app")
if not os.path.isdir(proj):
    raise SystemExit(0)
agent = os.environ["AGENT"]
best_mtime = -1.0
best_sid = ""
for path in glob.glob(os.path.join(proj, "*.jsonl")):
    try:
        with open(path) as f:
            first = f.readline()
        rec = json.loads(first)
    except Exception:
        continue
    if rec.get("customTitle") != agent:
        continue
    mt = os.path.getmtime(path)
    if mt > best_mtime:
        best_mtime = mt
        best_sid = rec.get("sessionId", "")
print(best_sid)
PY
}

# 找出某 agent tmux 窗口里所有 CLI 子进程的 PID (空格分隔)。
#
# 通过 adapter.process_name() 获取进程名 (CC="claude", Kimi="kimi"),
# 走 tmux pane_pid → /proc PPid 父子关系定位。
# 不依赖 procps (alpine/distroless 没有 pgrep / pstree)。
_lifecycle_pids_for_agent() {
  local agent="$1"
  local session bash_pid proc_name
  session=$(_lifecycle_tmux_session)
  bash_pid=$(tmux display-message -t "$session:$agent" -p '#{pane_pid}' 2>/dev/null)
  [[ -z "$bash_pid" ]] && return 0
  proc_name=$(python3 -m claudeteam.cli_adapters.resolve "$agent" process_name 2>/dev/null)
  [[ -z "$proc_name" ]] && proc_name="claude"
  BASH_PID="$bash_pid" PROC_NAME="$proc_name" python3 - <<'PY' 2>/dev/null
import os, glob
bash_pid = int(os.environ["BASH_PID"])
proc_name = os.environ["PROC_NAME"]
children = {}
comm_by_pid = {}
out = []
for d in glob.glob("/proc/[0-9]*"):
    try:
        pid = int(os.path.basename(d))
    except ValueError:
        continue
    try:
        with open(f"{d}/status") as f:
            st = f.read()
    except OSError:
        continue
    ppid = None
    for line in st.splitlines():
        if line.startswith("PPid:"):
            try:
                ppid = int(line.split()[1])
            except (IndexError, ValueError):
                ppid = None
            break
    if ppid is not None:
        children.setdefault(ppid, []).append(pid)
    try:
        with open(f"{d}/comm") as f:
            comm_by_pid[pid] = f.read().strip()
    except OSError:
        continue
stack = list(children.get(bash_pid, []))
seen = set()
while stack:
    pid = stack.pop()
    if pid in seen:
        continue
    seen.add(pid)
    if comm_by_pid.get(pid) == proc_name:
        out.append(str(pid))
    stack.extend(children.get(pid, []))
print(" ".join(out))
PY
}

# spawn_agent <name> — 冷启动一个全新 claude (新 session)
spawn_agent() {
  local agent="$1"
  [[ -z "$agent" ]] && { echo "❌ spawn_agent: 缺少 agent 名" >&2; return 2; }

  local session model
  session=$(_lifecycle_tmux_session)
  if ! model=$(PYTHONPATH="${PYTHONPATH:-}:$_LC_PROJECT_ROOT/src" python3 -m claudeteam.runtime.config resolve-model "$agent"); then
    echo "❌ spawn_agent: 解析 $agent 模型失败" >&2
    return 1
  fi

  if ! tmux has-session -t "$session:$agent" 2>/dev/null; then
    tmux new-window -t "$session" -n "$agent" -c "$_LC_PROJECT_ROOT" 2>/dev/null || {
      echo "❌ spawn_agent: 无法创建 tmux 窗口 $session:$agent" >&2
      return 1
    }
    sleep 0.5
  fi

  # —— [BLOCK 1 fix] 防御纵深: spawn 是冷启动, pane 应当只在 shell 状态. 误调
  # (重复 /hire / 手工脚本走错路径) 时, 用 pane_current_command 拦住, 避免把
  # spawn_cmd 打进已经活着的 CLI. 与 wake_agent 区别: spawn 走 return 1 (错误),
  # 因为冷启动 pane 已被占就是逻辑错误, 不是幂等 no-op.
  local pane_cmd
  pane_cmd=$(tmux display-message -t "$session:$agent" -p '#{pane_current_command}' 2>/dev/null)
  case "$pane_cmd" in
    bash|zsh|sh|"") : ;;
    *)
      echo "❌ spawn_agent: $agent pane 前台是 '$pane_cmd' (非 shell) — 拒绝重复 spawn" >&2
      return 1
      ;;
  esac
  # —— end [BLOCK 1 fix]

  local spawn_cmd
  spawn_cmd=$(python3 -m claudeteam.cli_adapters.resolve "$agent" spawn_cmd "$model")
  tmux send-keys -t "$session:$agent" "$spawn_cmd" Enter
  echo "🟢 spawn_agent: $agent (model=$model)"
  return 0
}

# suspend_agent <name>
# 严格顺序 (ADR §A.7 不变式):
#   1. 状态表写"休眠"  — router 看到这个状态后改走 wake_on_deliver 路径
#   2. 保存 session_id  — 必须在 kill 前抓,kill 之后 mtime 不再更新
#   3. kill claude pid — SIGTERM 1s 后兜底 SIGKILL
#   4. tmux 窗口保留   — pane 留 💤 提示,不删窗口
suspend_agent() {
  local agent="$1"
  [[ -z "$agent" ]] && { echo "❌ suspend_agent: 缺少 agent 名" >&2; return 2; }

  local session
  session=$(_lifecycle_tmux_session)

  # 1. 状态表 — 失败必须中止,绝不能在状态表没改之前杀进程
  if ! python3 "$_LC_SCRIPTS_DIR/feishu_msg.py" status "$agent" 休眠 "lazy-wake suspend" >/dev/null 2>&1; then
    echo "⚠️ suspend_agent: 状态表写入失败,中止 (避免 router 丢消息)" >&2
    return 1
  fi

  # 2. 抓 session_id 持久化 (kill 后 mtime 不变,提前抓更稳)
  local sid
  sid=$(_lifecycle_discover_session "$agent")
  if [[ -n "$sid" ]]; then
    _lifecycle_save_session "$agent" "$sid"
    echo "💾 suspend_agent: 保存 $agent session_id=${sid:0:8}"
  else
    echo "⚠️ suspend_agent: 未找到 $agent 的 session_id,wake 时将冷启动" >&2
  fi

  # 3. kill claude pid
  local pids
  pids=$(_lifecycle_pids_for_agent "$agent")
  if [[ -n "$pids" ]]; then
    # shellcheck disable=SC2086
    kill -TERM $pids 2>/dev/null || true
    sleep 1
    # shellcheck disable=SC2086
    kill -KILL $pids 2>/dev/null || true
    echo "🔪 suspend_agent: $agent claude pid=$pids 已 kill"
  fi

  # 4. tmux 窗口留 💤 提示 (pane 已经回到 bash 提示符)
  if tmux has-session -t "$session:$agent" 2>/dev/null; then
    tmux send-keys -t "$session:$agent" \
      "echo '💤 $agent 已休眠 (lazy-wake) — 收到消息由 router 自动唤醒'" Enter
  fi

  echo "💤 suspend_agent: $agent done"
  return 0
}

# wake_agent <name> — 从保存的 session 拉起,失败回退冷启动
# wake 后状态由 router::wake_on_deliver 在 inject 阶段刷新 (本函数只把状态从
# "休眠"挪到"待命",真正在做事的状态由 agent 自己 status 上报)。
wake_agent() {
  local agent="$1"
  [[ -z "$agent" ]] && { echo "❌ wake_agent: 缺少 agent 名" >&2; return 2; }

  local session model
  session=$(_lifecycle_tmux_session)
  if ! model=$(PYTHONPATH="${PYTHONPATH:-}:$_LC_PROJECT_ROOT/src" python3 -m claudeteam.runtime.config resolve-model "$agent"); then
    echo "❌ wake_agent: 解析 $agent 模型失败" >&2
    return 1
  fi

  # 已经活着 → no-op (并发 wake 防御)
  if [[ -n "$(_lifecycle_pids_for_agent "$agent")" ]]; then
    echo "ℹ️ wake_agent: $agent 已活,跳过"
    return 0
  fi

  # —— [BLOCK 1 fix] 第二道闸门: 不依赖 /proc 的 pane_current_command 兜底
  # 当上面的 _lifecycle_pids_for_agent 因 comm 截断 / setsid 切链 / Darwin 无
  # /proc 等原因假阴性时, 这一道用 tmux 自己看到的 kernel 事实拦住, 避免把
  # spawn_cmd 通过 send-keys 打进活着的 CLI 输入框 (污染 chat 内容).
  # 取不到 pane_cmd (空串, e.g. 窗口还没建) 时走 ok 分支, 等价于无新闸门, 回退
  # 到旧行为, 不引入新失败模式.
  local pane_cmd
  pane_cmd=$(tmux display-message -t "$session:$agent" -p '#{pane_current_command}' 2>/dev/null)
  case "$pane_cmd" in
    bash|zsh|sh|"")
      :  # pane 在 shell, 或读不到 — 安全 spawn
      ;;
    *)
      echo "⚠️ wake_agent: $agent pane 前台是 '$pane_cmd' (非 shell) — 跳过 spawn,避免 send-keys 打进活着的 CLI 输入框" >&2
      return 0
      ;;
  esac
  # —— end [BLOCK 1 fix]

  if ! tmux has-session -t "$session:$agent" 2>/dev/null; then
    tmux new-window -t "$session" -n "$agent" -c "$_LC_PROJECT_ROOT" 2>/dev/null || {
      echo "❌ wake_agent: 无法创建 tmux 窗口 $session:$agent" >&2
      return 1
    }
    sleep 0.5
  fi

  local sid resume_cmd spawn_cmd
  sid=$(_lifecycle_get_session "$agent")
  if [[ -n "$sid" ]] && resume_cmd=$(python3 -m claudeteam.cli_adapters.resolve "$agent" resume_cmd "$model" "$sid" 2>/dev/null); then
    tmux send-keys -t "$session:$agent" "$resume_cmd" Enter
    echo "🌅 wake_agent: $agent resume sid=${sid:0:8} (model=$model)"
  else
    spawn_cmd=$(python3 -m claudeteam.cli_adapters.resolve "$agent" spawn_cmd "$model")
    tmux send-keys -t "$session:$agent" "$spawn_cmd" Enter
    echo "🌅 wake_agent: $agent 冷启动 — 无 saved session 或 adapter 不支持 resume (model=$model)"
  fi

  # 状态从"休眠"挪到"待命",router 投递业务消息后会刷成"进行中"
  python3 "$_LC_SCRIPTS_DIR/feishu_msg.py" status "$agent" 待命 "lazy-wake awakened" >/dev/null 2>&1 || true

  return 0
}

# CLI 直接调用入口
if [[ "${BASH_SOURCE[0]}" == "${0}" ]]; then
  case "${1:-}" in
    spawn)   spawn_agent   "${2:-}" ;;
    suspend) suspend_agent "${2:-}" ;;
    wake)    wake_agent    "${2:-}" ;;
    *)
      echo "用法: bash $0 {spawn|suspend|wake} <agent>" >&2
      exit 2
      ;;
  esac
fi
