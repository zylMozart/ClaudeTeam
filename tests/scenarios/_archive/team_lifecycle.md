# Team lifecycle: start / hire / fire

## 场景
最小可演示团队启停：从 team.json 起整个团队（manager + workers），单独 hire 一个新 agent，fire 一个 worker。所有 pane 启动后跑各自的 CLI（Claude Code / Codex / Kimi），状态写入 local facts 表。

## 范围
- 类型：host-live （需要真 tmux + 至少一种 CLI 工具装好）
- 凭证：CLI 各自的 login（Claude Code / OpenAI / Moonshot）

## Given
- `tmux -V` 可用
- `team.json` 在 `$PWD`，含 `session` 和 `agents` 字段
- export `CLAUDETEAM_STATE_DIR=$PWD/state`
- 当前没有同名 tmux session

## When

```bash
# 起整个团队
claudeteam start

# 看 tmux 窗口
tmux list-windows -t MyTeam   # 应看到一个 window per agent

# 单独 hire 一个新 worker（要求 team.json 已含此 agent）
claudeteam hire worker_extra

# fire 一个 worker
claudeteam fire worker_kimi

# 看团队状态
claudeteam team
```

## Then
1. **start** 退出 0，stdout 包含 `🚀 created tmux session ...` + N 行 `→ <agent> spawned`
2. `tmux list-windows -t <session>` 列出每个 agent 一个 window
3. 每个 pane 进入对应 CLI 的 banner（`bypass permissions on` / `OpenAI Codex` / `Welcome to Kimi`）
4. `claudeteam team` 列出所有 agent 状态都是 `进行中 | initializing`
5. **hire worker_extra** 退出 0，新 window 出现，CLI banner 出现
6. **fire worker_kimi** 退出 0，window 消失（`tmux list-windows` 不再含），状态 `已停止 | fired`
7. 重跑 **start** 退出 1，提示 `already running`

## 证据（执行时填）

```
- T_start: …
- N_agents: …
- T_hire: …
- T_fire: …
- 结果: pass | fail
- 后续: …
```
