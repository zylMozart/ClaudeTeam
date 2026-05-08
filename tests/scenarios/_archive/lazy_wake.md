# Lazy wake: placeholder pane → CLI on first message

## 场景
某些 worker 平时空闲，全启动会浪费 token/credit。team.json 里给它们打 `"lazy": true` 标记，`claudeteam start` 给这些 agent 创建 tmux window 但**不**起 CLI；status 写成 `待命`。当第一条消息从 router 派给这个 agent 时，deliver 端先 `wake_if_dormant` —— 检测 pane 没有 ready marker，就用 adapter.spawn_cmd 起 CLI，等 banner 出现后再 inject。

非 lazy agent 完全不受影响（向后兼容）。

## 范围
- 类型：host-live （需要真 tmux + 至少一种 CLI 工具登好）
- 凭证：CLI 各自的 login

## Given
- `tmux -V` 可用
- `team.json` 至少含一个标了 `"lazy": true` 的 worker
- `runtime_config.json` 含真的 chat_id
- `CLAUDETEAM_STATE_DIR=$PWD/state`
- 当前没有同名 tmux session

## When

```bash
# team.json 例：
# {
#   "session": "Lazy",
#   "agents": {
#     "manager":   {"cli": "claude-code"},
#     "worker_lazy": {"cli": "claude-code", "lazy": true}
#   }
# }

claudeteam start
tmux list-windows -t Lazy
claudeteam team

# 在 Feishu 群里 @worker_lazy
# 然后看 router 日志 + tmux pane

claudeteam team
```

## Then
1. **start** 退出 0；输出含 `→ worker_lazy (claude-code) lazy-pane ready`
2. `tmux list-windows` 列出 manager + worker_lazy 两个 window
3. `claudeteam team` 显示 `worker_lazy | 待命 | lazy: CLI starts on first message`
4. 在群里 @worker_lazy 后：
   - router 日志含 `wake` 路径或 `spawn_cmd` 调用
   - worker_lazy pane 出现 Claude Code banner（`bypass permissions on`）
   - 消息成功 inject 到 pane（banner 之后能看到我的消息文本）
   - `claudeteam team` 状态变成 `进行中`
5. 第二条消息走快路径（已经 ready），不再 spawn
6. 关掉 pane（C-c + tmux kill-window 或 fire），下一条消息再次触发 wake

## 反例
- spawn 失败（CLI 工具没装）：deliver 打 `pane not ready; injecting anyway`，inject 会 fail，inbox 行还在
- ready_marker 30 秒没出现：wake 返回 False，仍然尝试 inject

## 证据（执行时填）

```
- T_first_message: …
- 是否触发 wake: pass | fail
- banner 出现耗时: …
- 第二条消息是否走快路径: pass | fail
- 后续: …
```
