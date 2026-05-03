# Round C — Real task assignment end-to-end

## 场景

打通"老板 → manager → workers → 汇总"完整循环。这是 ClaudeTeam 存在的
理由 —— 之前所有 round (A 修 identity init / B 修 image+post-compact+
multi-team+docker) 都是基础设施，Round C 验证基础设施真的 carry 一个
真实任务能干完、能汇总、能交付。

姊妹 round：之前所有 host-live smoke 都验证 routing 正确性 (消息到不到、
路径对不对)；Round C 验证 **任务能不能完成**（agent 跨多次回合协作、
manager 跟踪进度、最终给 boss 一个总结）。

## 范围

- 类型：host-live (full Feishu + tmux + 真 CLI panes + 真 agent LLM 派工)
- 凭证：`test-live-a` profile (cli_a961274ccb385cc4) + chat
  `oc_989e33567a4be168c7e7a286287a3965` (Round B 用过的同一组)
- 操作员：boss (人工驱动)
- 预期时长：30-60 min（取决于任务复杂度）

## Given

完整 B-series done (验过的 commit 范围 `ee0dc2f` 之后)。具体:

- `claudeteam up` 跑起来 4 panes (manager + worker_cc + worker_codex +
  worker_kimi)
- `claudeteam health` 全绿（除非 kimi 还在 429 quota，那就 ⚠️ 容忍）
- 每个 agent 都自报家门过一次（identity init prompt 完成）
- inbox / cursor / status 全部初始化为空

可选：

- 用 `claudeteam switch` 把 state_dir 切到独立的 RoundC team-data 目录
  避免污染之前的 RoundB 状态
- session 名建议 `RoundC` 或 `Smoke-2026-05-03b`

## When — 真任务

老板从飞书群里发：

```
@manager 我有个任务给团队：把当前 ClaudeTeam 的 README 翻译成英文，
存到 README.en.md。要求:
- 保留原结构和代码块
- 术语 (CLI / pane / chat) 不译
- 完成后由你汇总三个 worker 的差异，给我一份 review report

请把任务拆成 3 块，分配给三个 worker，并跟踪到完成。
```

manager 收到后预期行为:

1. **拆任务**：split README 三个 section (e.g. quickstart / commands / what's missing) → 写到 `claudeteam task create` 三条
2. **派单**：`claudeteam send worker_cc manager "翻译 quickstart 部分..."` 三遍
3. **跟踪**：每隔几分钟 `claudeteam team` / `claudeteam task list` 看进度
4. **汇总**：三个 worker 的 PR 都到了之后，diff 各自译文风格 / 术语一致性，
   给 boss 一份 markdown 报告

每个 worker 收到 inbox row → 起手干活 → 完成后 `claudeteam say <name>
"PR 1: README quickstart 翻译完成，存到 README.en.part1.md"` 报到群里。

## Verification status (post-R101)

What this playbook is testing that *hasn't already been verified
piece-meal*: agent **collaboration quality** (manager actually splits
tasks, worker reports progress with `say` not `send`, manager actually
diffs the three译文 vs concatenating). The **infrastructure pieces**
are independently green via the boss-extended push (R79-R101):

- routing (boss → router → manager pane): R79 cards smoke + R85 manager
  identity v2 reidentify confirm
- inbox / status pipe: R83-R84 memory tests + R96 forget per-agent
  isolation
- `/team` / `/recall` / cards: R80, R95 host-live cards landed
- watchdog daemon stability: R98 cooldown alert smoke
- lark-cli speed: R86 73s → 0.6s

So gates G3 (worker 收单), G4 (进度自报), G6 (manager 汇总) are the
HIGH-SIGNAL ones for Round C. G7 time-box is comfortable now that
lark-cli is fast. G9 catchup was already proven in router-restart
smokes.

## Then — Sub-gate（待 smoke 时填）

| Gate | 现象 | 判 |
| --- | --- | --- |
| **G1 manager 拆任务** | `claudeteam task list` 显示 3 条 T-XX，assignee 各对应一个 worker | ⏳ |
| **G2 manager 派单可见** | 群里 `[manager] 已分派给 worker_cc/codex/kimi 各一份子任务` | ⏳ |
| **G3 worker 收单** | 三个 worker pane 都能看到自己的 inbox row + 起手 say "收到，开始翻译 X" | ⏳ |
| **G4 进度自报** | 三个 worker 中至少 2 个在 30 分钟内 say 一次进度（"译完 quickstart 部分，开始 commands"） | ⏳ |
| **G5 worker 完工** | 三个 worker `claudeteam task done T-XX` + `claudeteam say <name> "PR <name>: ... 完成"` | ⏳ |
| **G6 manager 汇总** | manager 群里发完整 review report：每个 worker 的译文风格特点 + 术语统一情况 + 推荐 merge 哪一份 | ⏳ |
| **G7 时间盒** | 全程 ≤ 60 min（包括 LLM 思考 + tool use 的等待时间） | ⏳ |
| **G8 无操作员介入** | boss 第一句任务 prompt 之后，无需人工救场（manager 无 reidentify、worker 无 stop+rehire） | ⏳ |
| **G9 catchup 抗中断** | mid-run 故意 SIGTERM router 一次，重启后 cursor 接续读、零消息丢失 | ⏳ |
| **G10 manager 主动报错** | 如果某 worker 卡 quota / 长时间没动，manager 主动在群里说 "X agent 似乎卡住，建议 reidentify" | ⏳ |

## Why this is here

CLAUDE.md 工作单 item 20，最后一项。是整个 ClaudeTeam 设计的"真人考"
—— 不是测 routing 对不对（那些 round A/B 已经测了），是测 **agent
能不能真协作干活**。即:

- manager 是不是真在拆任务（vs 直接转发给一个 worker）
- worker 是不是真按照 inbox 走（vs 路径乱）
- 进度更新是不是真在 say 而不是 send 到 inbox（这是 Round B G5.a 留下
  的 ⚠️）
- manager 汇总是不是真有 review 价值（vs 简单 concat）

如果 G6 manager 汇总质量差，下一轮要回头改 identity.md 里 manager 的
"汇总" 段落（让指令更具体），而不是改 router 代码。这是测 **prompt 工
程**，router 已经基本 done。

## 已知风险

1. **kimi 429 quota** ：Round B G2.d 留下的，kimi 这条线在那次 smoke 里
   就没 ack 过。如果还没换 quota，kimi 这一份会卡住，G3/G4/G5 会有 1 个
   ⚠️。可接受 —— 不影响 cc / codex 的协作验证。

2. **worker 用 send 而不是 say**：Round B G5.a 看到的 LLM 行为问题。
   identity.md 已经在 _WORKDIR_RULE 之后加了 send vs say 的说明
   (commits `246c2f1` + `490e00d`)，但 LLM 还是可能犯。如果 G3/G4
   出现这种偏差，记下来作为 prompt 工程改进点 —— 不算 router bug。

3. **lark-cli 延迟（已修）**：早先 memory 记录"~73s round-trip"是错的
   —— 那是 npx 包查找开销而不是网络。Round-86 (`feishu/lark._resolve_cli_prefix`)
   改成直连 binary 后实测每次 send 约 **0.6s**（macOS host）。9 个 say
   累计 < 10s，不再是时间盒主导项。
   - 验证当前部署是否走 fast path：`time lark-cli --profile <p> im +chat-search --as bot --query x`，
     秒回即正确。如果 ~73s 说明 resolver 落到 npx 兜底，需 `npm i -g @larksuite/cli`
     或显式 `CLAUDETEAM_LARK_CLI_BIN=/path` 修正。

## Out of scope

- **多 manager**：现在团队只一个 manager。多 manager 协作不在此 round。
- **真改代码 + 真 PR**：Round C 任务限定在文档/翻译类——避免 worker 真
  `git push` 出去。代码 PR 类放到 Round D。
- **跨 chat 任务**：所有 say 都在同一个 group chat，不测 1-on-1 私聊。

## 后续 Round D 候选

- 真代码 PR：boss 派一个真 bug fix，worker 真改代码 + 真跑 tests + 真
  push 到 fork branch + 给出 PR link
- 多任务并发：boss 同时派 5 个任务，看 manager 调度 / 优先级
- worker 间 peer review：worker_codex 完工后让 worker_cc review (需要
  manager 编排成 2 层 inbox)
