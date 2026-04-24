#!/bin/bash
# 共享库: tmux 团队拉起过程中的 Claude UI 启动探测与诊断。
#
# 被 scripts/start-team.sh 和 scripts/docker-entrypoint.sh 同时 source。
# 不能单独运行 —— 依赖调用方已设置以下 shell 变量:
#   SESSION  — tmux session 名
#   AGENTS   — bash array,agent 名单
#
# 设计原则:
#   1. 库函数不调用 exit,退出/降级决策权永远留给调用方。
#      (容器场景下 exit 会被 restart: unless-stopped 循环,诊断信息会丢。)
#   2. probe 结果通过全局 FAILED_AGENTS 数组 + return code 两种形式返回,
#      方便调用方既能判定成败,也能拿到失败名单做定制化处理。
#   3. UI 特征串 'bypass permissions on' / '? for shortcuts' 和 15 秒上限
#      是现有 baseline,两个入口都经过验证,这里保持一致。

# ── lazy-mode 白名单决策 (lazy_wake_v2 §A.2) ───────────────────
# 由 start-team.sh / docker-entrypoint.sh 共享,保证宿主与容器对"哪些 agent
# 真跑 claude / 哪些做 💤 占位"的判断完全一致。新增白名单角色只改这一处。
#
# 调用方约定:
#   - 在 source 本库之前已设置 LAZY_MODE 变量 (on/off)。未设视为 off。
#   - 业务策略: lazy-mode = on 且 agent 不在 LAZY_WHITELIST_AGENTS → 应跳过
#     spawn claude,只建窗口留 💤 banner,等 router::wake_on_deliver 唤醒。

LAZY_WHITELIST_AGENTS=(manager supervisor)

is_lazy_whitelist() {
  local name="$1" w
  for w in "${LAZY_WHITELIST_AGENTS[@]}"; do
    [[ "$name" == "$w" ]] && return 0
  done
  return 1
}

# should_skip_agent_in_lazy_mode <agent>
#   返回 0 = 应跳过 spawn (lazy-mode on 且非白名单)
#   返回 1 = 应正常 spawn (lazy off 或在白名单)
should_skip_agent_in_lazy_mode() {
  local agent="$1"
  [[ "${LAZY_MODE:-off}" = "on" ]] && ! is_lazy_whitelist "$agent"
}

# ── per-role 模型预解析 (lazy_wake_v2 §B) ─────────────────────
# resolve_all_agent_models
# 前置条件: 调用方已填充 bash array $AGENTS (来自 team.json)。
# 效果:
#   填充全局关联数组 AGENT_MODELS[agent]=model_id,基于
#   scripts/config.py resolve-model 的 fallback 链 + 白名单校验。
#   任一 agent 解析失败立即返回,保证"起了一半才发现非法 model"不会发生。
# 返回:
#   0 — 全部解析成功,AGENT_MODELS 可直接用于 claude --model $...
#   1 — 至少一个失败,失败的 agent 名写入全局 FAILED_MODEL_AGENT,
#       错误详情已打到 stderr。调用方应据此 exit(库不自行 exit)。
resolve_all_agent_models() {
  local agent model
  FAILED_MODEL_AGENT=""
  unset AGENT_MODELS
  declare -gA AGENT_MODELS
  for agent in "${AGENTS[@]}"; do
    if ! model=$(python3 scripts/config.py resolve-model "$agent" 2>&1); then
      echo "❌ 解析 $agent 的模型失败: $model" >&2
      echo "   请检查 team.json 中 $agent 的 model 字段,或 CLAUDETEAM_DEFAULT_MODEL 环境变量。" >&2
      FAILED_MODEL_AGENT="$agent"
      return 1
    fi
    AGENT_MODELS[$agent]=$model
  done
  return 0
}

# print_agent_models_table
# 打印 '📋 模型分配' 表。调用方在 bring-up 前展示一遍,方便排错时一眼
# 看到每个 agent 实际用的是哪个 model。
print_agent_models_table() {
  local agent
  echo "📋 模型分配:"
  for agent in "${AGENTS[@]}"; do
    echo "     $agent → ${AGENT_MODELS[$agent]}"
  done
}

# probe_claude_agents [max_attempts]
# 轮询检查 agent 窗口里的 Claude UI 是否已就绪。默认遍历 $AGENTS;
# 若调用方 export 了 PROBE_AGENTS="name1 name2 ..." 则只 probe 该子集。
# lazy-mode 下只有白名单 agent 真正跑 claude,需要通过 PROBE_AGENTS 缩圈,
# 否则占位窗口 (💤 待 wake) 会被误判为启动失败。
# 返回:
#   0 — 所有被 probe 的 agent 窗口都出现了 Claude UI 特征串
#   1 — 至少一个 agent 失败,名单写入全局 FAILED_AGENTS 数组
probe_agents() {
  local max_attempts="${1:-15}"
  local attempt agent pane ready_pattern
  local -a agents_to_probe
  if [ -n "${PROBE_AGENTS:-}" ]; then
    # shellcheck disable=SC2206
    agents_to_probe=( ${PROBE_AGENTS} )
  else
    agents_to_probe=( "${AGENTS[@]}" )
  fi
  local _bringup_scripts_dir
  _bringup_scripts_dir="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
  for attempt in $(seq 1 "$max_attempts"); do
    FAILED_AGENTS=()
    for agent in "${agents_to_probe[@]}"; do
      pane=$(tmux capture-pane -t "$SESSION:$agent" -p -S -60 2>/dev/null)
      ready_pattern=$(python3 -m claudeteam.cli_adapters.resolve "$agent" ready_markers 2>/dev/null)
      if echo "$pane" | grep -q "$ready_pattern"; then
        :
      else
        FAILED_AGENTS+=("$agent")
      fi
    done
    if [ ${#FAILED_AGENTS[@]} -eq 0 ]; then
      return 0
    fi
    if [ "$attempt" -lt "$max_attempts" ]; then
      sleep 1
    fi
  done
  return 1
}

# 向后兼容别名
probe_claude_agents() { probe_agents "$@"; }

# diagnose_failed_agents
# 打印首个失败 agent 的 tmux pane 尾部 + 常见根因分析。
# 只读操作,不改变任何状态,不 exit —— 是否继续由调用方决定。
diagnose_failed_agents() {
  local diag_agent diag_pane
  echo ""
  echo "❌ 以下 agent 的 Claude UI 未能启动: ${FAILED_AGENTS[*]}"
  diag_agent="${FAILED_AGENTS[0]}"
  diag_pane=$(tmux capture-pane -t "$SESSION:$diag_agent" -p -S -30 2>/dev/null)
  echo "   窗口最后几行内容 ($diag_agent):"
  echo "$diag_pane" | tail -6 | sed 's/^/     | /'
  if echo "$diag_pane" | grep -q "root/sudo privileges"; then
    echo ""
    echo "   ↳ 根因: Claude Code 拒绝以 root 启动 --dangerously-skip-permissions。"
    echo "     检查: IS_SANDBOX=1 是否被透传; Claude Code 版本是否识别该变量。"
  elif echo "$diag_pane" | grep -q "command not found\|No such file"; then
    echo ""
    echo "   ↳ 根因: PATH 里没找到 claude。在当前 shell 跑 'which claude' 确认。"
  else
    echo ""
    echo "   ↳ 未识别的启动失败。attach tmux 手动查看窗口内容。"
  fi
}
