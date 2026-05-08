# Re-identify a running agent

## 场景

agent 已经活着，但记忆乱了 —— 比如刚 `/compact` 过、刚 `/clear` 过、或者
boss 刚改了 `team.json` 里它的 role/model。这个时候不需要重启 pane（重启
会丢 tmux scrollback、重启 CLI 用大量配额），只想"让它重新读一遍 identity，
重新报到"。`claudeteam reidentify <agent>` 干这件事 —— 把 init prompt
重新注入 pane，让 agent 从 `agents/<name>/identity.md` 重新读取、回 inbox、
upsert status。

姊妹用例：B.2 的 `/compact <agent>` 飞书斜杠命令在 router 层面调度 45s
后 inject 同一个 init prompt，自动化版本的 reidentify。

## 范围

- 类型：host-live (tmux + 真 CLI pane；不一定要飞书在线)
- 凭证：无（reidentify 只动 pane，不发飞书）
- 操作员：boss / manager（或 cron / watchdog 后续场景）

## Given

- `claudeteam up` 已经把团队拉起来，`claudeteam health` 全绿
- `worker_cc` 的 pane 在 tmux session `ClaudeTeam:worker_cc` 里活着
- `worker_cc` 的 pane 当前已经响应了若干消息 → context 历史一长串

## When

```bash
# 假设 worker_cc 刚刚执行了 /compact 让自己压缩上下文
# 或者 boss 刚把 team.json 里 worker_cc 的 role 从 "Claude Code 员工"
# 改成了 "Claude Code Senior 员工" 想让 agent 立刻 pick up

claudeteam reidentify worker_cc

# 全员一起刷新（R91：替代 `for a in ...; do claudeteam reidentify $a; done`）。
# 跳过没活 pane 的（lazy / fired），逐 agent 打印一行结果，整体 rc=0
# 当且仅当所有 agent 都成功；任何一个 skip 或 inject fail 时 rc=1。
claudeteam reidentify --all
```

## Then

stdout（单 agent）:

```
✅ re-injected identity init into worker_cc (pane: ClaudeTeam:worker_cc)
```

stdout（`--all`）:

```
🔁 reidentify all (4 agents in ClaudeTeam):
  ✅ manager (pane: ClaudeTeam:manager)
  ✅ worker_cc (pane: ClaudeTeam:worker_cc)
  ✅ worker_codex (pane: ClaudeTeam:worker_codex)
  ⏭  worker_kimi: no pane in session ClaudeTeam
reidentified 3/4 agents
```
（rc=1 because not 100%; rc=0 only when N/N）

worker_cc 的 tmux pane 立刻收到一段 init prompt，提示它：
- 你是 worker_cc
- 读 `agents/worker_cc/identity.md`
- 跑 `claudeteam inbox worker_cc` 拉未读
- 跑 `claudeteam status worker_cc 进行中 "ready"`
- 一行 ack（name + state + unread）

worker_cc 的 LLM 响应（同步在 chat 里看到）应该体现新的 role/model — 比如
`team.json` 之前改过 role 字段，agent 自报家门时会念新 role 出来。

错误路径:

| 输入 | exit | stderr |
| --- | --- | --- |
| `claudeteam reidentify` | 1 | `usage: claudeteam reidentify <agent>` |
| `claudeteam reidentify ghost` | 1 | `❌ unknown agent: ghost (not in team.json)` |
| `claudeteam reidentify worker_cc`（session 不在）| 1 | `❌ tmux session ClaudeTeam not running; run claudeteam up first` |
| `claudeteam reidentify worker_cc`（pane 被 fire 掉）| 1 | `❌ worker_cc has no pane in session ClaudeTeam (was it fired? try claudeteam hire worker_cc)` |

## Why this is here

CLAUDE.md 工作单 item 14 (post-compact identity reread) 一开始的设计是
slash `/compact` 触发自动 reidentify（commit `ab90bd0`）。手动入口
`claudeteam reidentify` 早一步落地 —— 给三种情况留口子：

1. **boss 改了 team.json**：想立刻让某个 agent 重读自己的 role/model
2. **agent 自己跑飞了**：context 乱、人格丢了、自己 `/clear` 但没自动重注入
3. **CI/cron 巡检**：定期 reidentify 全员防止 long-lived agents drift

跟 `/compact` 的 background reidentify 是同一个 `identity.init_prompt` +
`tmux.inject` 路径，只是触发器不同。

## Out of scope

- **重启 CLI**：reidentify 不动 pane 进程，要重启用 `claudeteam fire <agent>
  && claudeteam hire <agent>`。前者会丢 tmux scrollback 历史。
- **跨 session 批量**：`--all` 只刷当前 `team.json` 对应的 session。
  多 team 部署要用 `claudeteam switch` 切到下一个 team-data 再 `--all`，
  没有一次性跨 team 的口子。
