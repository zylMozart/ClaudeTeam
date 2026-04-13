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

# probe_claude_agents [max_attempts]
# 轮询检查 $AGENTS 中所有 agent 窗口里的 Claude UI 是否已就绪。
# 返回:
#   0 — 所有 agent 窗口都出现了 Claude UI 特征串
#   1 — 至少一个 agent 失败,名单写入全局 FAILED_AGENTS 数组
probe_claude_agents() {
  local max_attempts="${1:-15}"
  local attempt agent pane
  for attempt in $(seq 1 "$max_attempts"); do
    FAILED_AGENTS=()
    for agent in "${AGENTS[@]}"; do
      pane=$(tmux capture-pane -t "$SESSION:$agent" -p -S -60 2>/dev/null)
      if echo "$pane" | grep -q "bypass permissions on\|? for shortcuts"; then
        :
      else
        FAILED_AGENTS+=("$agent")
      fi
    done
    if [ ${#FAILED_AGENTS[@]} -eq 0 ]; then
      return 0
    fi
    # 最后一次不 sleep,让调用方立刻拿到结果
    if [ "$attempt" -lt "$max_attempts" ]; then
      sleep 1
    fi
  done
  return 1
}

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
