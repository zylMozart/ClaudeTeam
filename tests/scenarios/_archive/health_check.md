# `claudeteam health` — 部署状态一键快照

## 场景

`claudeteam health` 给老板和救火的 oncall 一个清晰的"现在到底有没有跑起来"
快照。一次跑完按 `paths / config / binaries / env / tmux / daemons /
router state / memory` 八段输出，每行带 ✅ / ❌ / ⚠️ 标记。任何一段红就
exit 1，让自动化脚本能拉警报。

## 范围

- 类型：仅本机命令（无 tmux 与守护进程时也能跑出红/黄）
- 凭证：无

## 前置条件

- `claudeteam` CLI 已装
- `CLAUDETEAM_STATE_DIR` 指向某个测试目录

## 操作

```bash
# 1) 啥都没起来：全是红和黄
claudeteam health

# 2) 跑完 init 并填了 chat_id：config 段绿；tmux 与 daemons 仍是红/黄
claudeteam init
$EDITOR runtime_config.json   # 填进 chat_id
claudeteam health

# 3) 起 tmux 与 agent：tmux 段大部分绿
claudeteam start
claudeteam health

# 4) 起 router 与 watchdog：daemons 段绿
claudeteam up                  # 等价于 start + router + watchdog
claudeteam health
```

## 期望

1. **第一次**：`team.json missing` + `runtime_config.json missing` + 至少 1 红，exit 1
2. **第二次**：`team.json: N agent(s)` ✅（N 取决于你的 team.json，默认 init 给 3：manager + worker_cc + worker_codex）；`chat_id: oc_xxx` ✅；tmux ❌；exit 1
3. **第三次**：`tmux session: <名字>` ✅；每个 agent `pane ready` ✅，或者 `pane up but CLI not ready yet` ⚠️（codex 弹更新时常见）；daemons ⚠️
4. **第四次**：`router: alive (PID)` + `watchdog: alive (PID)` ✅；如果在第 3 步之后还发过消息，`router cursor` ✅；exit 0，末尾 `✅ all green` 或 `⚠️ no errors, N warning(s)`

## 反例

- `team.json` 损坏（不是合法 JSON）：`team.json parse error: ...` 红，exit 1
- `runtime_config.json` 没有 chat_id 字段：`empty chat_id` 红，exit 1
- 某个 pane 没装对应 CLI（如 codex 没装）：`binaries: ❌ codex: not found` 红
- 某个 pane 没装且 CLI 也没登录：pane up 但没 ready marker，⚠️ 不影响 exit
- router 死了但 pid 文件没清：`pid file present but process dead` 红

## 看 router/watchdog 守护进程的真实状态

提交 `c0996a5` 之后，watchdog 监管的两个守护进程都把 stdout 与 stderr 写到
state 目录下的 log 文件，append 模式：

```bash
tail -50 state/router.log    # 最近的事件分类、slash 派发、send_card 结果
tail -50 state/watchdog.log  # 守护进程主循环、respawn、orphan 清理
```

如果 health 显示 router alive 但你怀疑它实际在丢消息，先看这两份 log——
比起重启再观察更省时间。Pre-`c0996a5` 这两份文件不存在，那种部署只能
SIGTERM router 再重启来翻案。

## 已知风险

1. **本机部署的特有警告**——`/usage` 段会因 macOS 把 claude OAuth 存在
   keychain 而不是文件，显示 "Claude usage 读取失败"。这条不计入 health
   的红/黄，是 `/usage` 卡片自身的渲染状态
2. **lark_profile 留空的 ⚠️**——只要 `CLAUDETEAM_LARK_SEND_AS=bot` 在
   shell env 里，发送链路其实工作正常；这条只是提醒以防有人没设环境变量

## 不在范围

- 看用量：`claudeteam usage`，看 [usage_snapshot.md](usage_snapshot.md)
- 看团队成员状态：`claudeteam team`，看 [team_overview_and_workspace.md](team_overview_and_workspace.md)

## 证据（跑的时候填）

```
- 第一次 health 执行时间 T1: …
- 各阶段 exit code: 1, 1, 1, 0
- 各阶段红/黄/绿计数: …
- state/router.log 是否存在: yes/no（用来判断是 c0996a5 前还是后）
- 备注: …
```
