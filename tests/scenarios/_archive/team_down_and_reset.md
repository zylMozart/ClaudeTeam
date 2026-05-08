# Team teardown: `claudeteam down` vs `claudeteam reset`

## 场景

team 在跑，需要停。两条路：

- `claudeteam down` — 优雅关停 daemons + tmux session，**保留 state**
  （inbox / status / cursor / identity 文件全留着）。重启 `claudeteam up`
  能立刻接着干，inbox 历史和 router cursor 都还在。
- `claudeteam reset` — 关停 + **wipe state**。inbox 清零、cursor 清零、
  pid 文件清零、identity.md 清零。配置文件 (`team.json` / `runtime_config.json`)
  保留。下次 `up` 等于全新开始（agent 不记得历史）。

90% 的情况用 down；reset 留给 demo / smoke / 状态污染需要清盘。

## 范围

- 类型：local-only
- 凭证：无
- 操作员：boss / 任一开发者

## Given

- `claudeteam up` 已经把 team 拉起来过
- `$CLAUDETEAM_STATE_DIR` 下有 `facts/inbox.json`, `facts/status.json`,
  `agents/<name>/identity.md`, `router.pid`, `router.cursor` 等文件

## When — 优雅关停（保留 state）

```bash
claudeteam down
```

输出（典型，无错）:

```
🛑 watchdog: pid 12346 stopped
🛑 router: pid 12345 stopped
🛑 tmux session ClaudeTeam killed
✅ team down
```

之后 `ls $CLAUDETEAM_STATE_DIR/facts/` 仍然能看到 `inbox.json`，inbox
历史完整保留。`claudeteam up` 重新启动后 router catchup 会从 cursor
继续读 Feishu，不丢消息。

## When — 全清盘 reset

```bash
# 交互模式：会提示 y/N 确认
claudeteam reset

# 自动化：跳过确认
claudeteam reset --yes
```

输出:

```
→ stopping daemons + tmux session
🛑 watchdog: pid 12346 stopped
🛑 router: pid 12345 stopped
🛑 tmux session ClaudeTeam killed
✅ team down
🗑  wiped /path/to/state
✅ reset complete (config files preserved)
```

之后 `ls $CLAUDETEAM_STATE_DIR/` → 目录不存在（被 rmtree 掉了）。
`team.json` 和 `runtime_config.json` 还在工作目录里。`claudeteam up`
能直接起，但 inbox/status/cursor 全部清零。

## Then — 校验差异

| 行为 | `down` 后 | `reset` 后 |
| --- | --- | --- |
| `team.json` / `runtime_config.json` | 保留 | 保留 |
| `state/facts/inbox.json` | 保留 | **删除** |
| `state/facts/status.json` | 保留 | **删除** |
| `state/router.cursor` | 保留 | **删除** (下次 up 走 catchup-on-restart 从空 cursor 开始) |
| `state/agents/*/identity.md` | 保留 | **删除** (下次 up 由 start 重新渲染) |
| pid 文件 | 关停时清掉了（它们是 daemon 的锁，daemon 死了就该清）| 同上 |
| tmux session | 杀掉 | 杀掉 |
| 飞书 chat 历史 | 不动（远端） | 不动（远端） |

## 错误路径

- `claudeteam reset`（不带 `--yes` 且不在 TTY 里）→ exit 1, stderr `aborted`
- `claudeteam down`（其实没什么在跑）→ exit 0，每行打印 `⏭ ...: no pid file`，
  最后 `✅ team down`
- 从已 down 状态再 `down` 一次 → 幂等，依旧 exit 0

## Why this is here

`down` 是日常每天结束 / 切团队前必跑；`reset` 是少数情况下把环境恢复到
初始状态用。两者经常被混淆 —— "是不是该 reset" → 99% 不是，down 就够了。
playbook 把差异表格化，避免误用 reset 砍掉历史。

`reset` 不动 `team.json` / `runtime_config.json` 是关键设计：reset 是清
**state**，不是清 **config**。要重新换 team 用 `claudeteam init --force`，
要切 team 用 `claudeteam switch`。

## Out of scope

- **跨主机的 reset**：`reset` 只清当前 host 的 state_dir。多主机部署时
  各 host 自己跑各自的 reset。
- **保留 inbox 但清 cursor**：现在 reset 是全清。要细粒度的话手工
  `rm $CLAUDETEAM_STATE_DIR/router.cursor`，没 wrapper。
