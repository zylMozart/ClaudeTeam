# Team status overview + workspace tail

## 场景
管理员 / 老板要快速 1) 看团队所有 agent 当前状态、2) 翻某个 agent 的最近事件审计。验证 `team` 横向读 status / `workspace` 纵向读 log 这两条读侧路径。

## 范围
- 类型：host-only
- 凭证：none

## Given
- export `CLAUDETEAM_STATE_DIR=$PWD/state`
- 已通过 `claudeteam status` / `claudeteam log` 写过几条数据

## When

```bash
# 三个 agent 上报状态
claudeteam status worker_a 进行中 "处理 X"
claudeteam status worker_b 已完成 "Y 完成"
claudeteam status worker_c 阻塞 "等审批" "需要老板批准 budget"

# worker_a 留下 5 条审计日志
for i in 1 2 3 4 5; do
  claudeteam log worker_a info "step $i complete"
done

# 读
claudeteam team
claudeteam workspace worker_a
claudeteam workspace worker_a --limit 2
```

## Then

1. **team** 退出 0，stdout 三行，按字母升序：
   - `worker_a  进行中  处理 X  (...ago)`
   - `worker_b  已完成  Y 完成  (...ago)`
   - `worker_c  阻塞    等审批  ⛔ 需要老板批准 budget  (...ago)`
2. **workspace worker_a**（默认 limit=20）退出 0，列出 5 条 step1..step5
3. **workspace worker_a --limit 2** 退出 0，标题 `last 2 log entries`，列出最后两条 (step 4, step 5)
4. `state/facts/status.json` 有 3 个 agent 项；`state/facts/logs.jsonl` 有 5 行（worker_a）

## 证据（执行时填）

```
- T_team: …
- T_workspace_default: …
- T_workspace_limit2: …
- 结果: pass | fail
- 后续: …
```
