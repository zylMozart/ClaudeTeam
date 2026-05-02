# `claudeteam health`: one-shot deployment-state check

## 场景
`claudeteam health` 给老板（和救火的 oncall）一个清晰的"现在到底有没有跑起来"快照。一次跑完会按 `paths / config / tmux / daemons / router state` 五段输出，每行带 ✅ / ❌ / ⚠️ 标记。任何一个红就 exit 1，让自动化脚本能拉警报。

## 范围
- 类型：host-only（无 tmux/daemons 时也能跑出红/黄）
- 凭证：none

## Given
- `claudeteam` CLI 已装
- `CLAUDETEAM_STATE_DIR` 指向某个测试目录

## When

```bash
# 1) 啥都没起来：全是红 + 黄
claudeteam health

# 2) 跑完 init 并填了 chat_id：config 段绿；tmux/daemons 仍红/黄
claudeteam init
# 编辑 rc.json 填 chat_id
claudeteam health

# 3) 起 tmux + agents：tmux 段大部分绿
claudeteam start
claudeteam health

# 4) 起 router + watchdog：daemons 段绿
claudeteam router &
claudeteam watchdog &
sleep 2
claudeteam health
```

## Then
1. **第一次**：`team.json missing` + `runtime_config.json missing` + 至少 1 红，exit 1
2. **第二次**：`team.json: 4 agent(s)` ✅；`chat_id: oc_xxx` ✅；tmux ❌；exit 1
3. **第三次**：tmux session ✅；每个 agent `pane ready` ✅ 或 `no CLI ready marker` ⚠️；daemons ⚠️
4. **第四次**：`router: alive (PID)` + `watchdog: alive (PID)` ✅；如果在第 3 步之后还发过消息，cursor ✅；exit 0 + 末尾 `✅ all green`

## 反例
- `team.json` corrupt（不是合法 JSON）：输出 `team.json parse error: ...` 红，exit 1
- `runtime_config.json` 没有 chat_id 字段：`empty chat_id` 红，exit 1
- 某 pane 没装对应 CLI（codex 没登录）：pane up 但没 ready marker，黄不影响 exit code
- router 死了但 pid 文件没清：`pid file present but process dead` 红

## 证据（执行时填）

```
- T_health_first: …
- 各阶段 exit code: 1, 1, 1, 0
- 各阶段红/黄/绿计数: …
- 后续: …
```
