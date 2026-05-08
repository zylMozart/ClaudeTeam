# Agent status snapshot + audit log

## 场景
agent 用 `status` 上报当前在做什么，用 `log` 留下事件审计轨迹。同步性最弱的两条命令：写过去就不再看，但需要可被巡视/复盘工具回读。本条验证写读最小闭环。

## 范围
- 类型：host-only
- 凭证：none

## Given
- export `CLAUDETEAM_STATE_DIR=$PWD/state`
- 该目录下没有 `state/facts/status.json` 和 `state/facts/logs.jsonl`

## When

```bash
# status 三模式
claudeteam status worker 进行中 "处理 X 任务"
claudeteam status worker          # 读
claudeteam status worker 阻塞 "等审批" "需要老板批准 budget"

# log 多条
claudeteam log worker info "started X"
claudeteam log worker task "完成 step 1" "TASK-7"
claudeteam log worker error "step 2 失败"
```

## Then

1. 第一次 `status` 退出 0，stdout 含 `worker → 进行中: 处理 X 任务`
2. `status worker`（读）退出 0，stdout 含 `worker: 进行中 | 处理 X 任务`
3. `status` 阻塞 模式 退出 0，stdout 含 `⛔ 需要老板批准 budget`
4. `state/facts/status.json` 中 `agents.worker.blocker == "需要老板批准 budget"` 且 `status == "阻塞"`（最后一次写覆盖）
5. 三次 `log` 各退出 0，每行 stdout 含 `logged: worker/<kind>  [log_xxx]`
6. `state/facts/logs.jsonl` 三行，按写入顺序，每行 JSON 含正确的 agent / type / content / ref / created_at

## 证据（执行时填）

```
- T_status_set: …
- T_status_show: …
- T_log_x3: …
- 结果: pass | fail
- 后续: …
```
