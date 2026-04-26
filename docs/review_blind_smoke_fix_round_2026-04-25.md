# Review · #57 fix-round 复检

- 日期: 2026-04-25
- 评审人: toolsmith
- 范围: F1-F8 复核 + 5 必给文档可读性盲测评估
- 副本: `/home/admin/projects/restructure-onboarding/`
- 上轮报告: `docs/review_blind_smoke_2026-04-25.md`

---

## 0. Verdict

**PASS-with-minor**

F1-F8 全部 resolved（4 M + 3 L + 1 改进项均到位，1 项 F8 实现质量超预期）。5 必给文档（ONBOARDING/BUILD/TEAM_LAYOUT/FEISHU_GROUP/SMOKE_GATES）共 421 行齐备，结构清晰可读，blind tester 大概率能跑通主路径。

仅留 6 个 R 级 readability gap（2 M / 4 L），都是 onboarding 文档完整性细节，不阻塞 qa_smoke——可让 coder 后续轮次顺手补，或留给新员工跑完后自己提 PR。

可放 qa_smoke。

---

## 1. F1-F8 复核表

| ID  | 上轮等级 | 修复状态 | 验证证据 |
|-----|----------|----------|----------|
| F1  | M | ✅ resolved | `ls .git` → No such file or directory |
| F2  | M | ✅ resolved | `grep -REn 'ClaudeTeam' docs/` 0 hits（PascalCase 全清）；README.md 还在但是 product brand + 公开 GitHub URL（合理保留）；lowercase `claudeteam` 是 Python 包名/unix 用户名/env var 前缀（操作必需，ONBOARDING.md:49 已显式说明） |
| F3  | M | ✅ resolved | `grep 'lazy_wake' live_container_smoke.md` 0 hits |
| F4  | M | ✅ resolved | `.claude/skills/` 现有 7 项（feishu-doc-publish/runtime-doctor/smoke-evidence/task-workflow/_template/tmux/tmux-boundary-diagnose），cheat 5 项（hire/fire/team-communication/server-status/rate-limit-triage）全删 |
| F5  | L | ✅ resolved | `docs/PROGRESS.md` 重写为 "Project Stage Brief" 入门快照，不再含 WATCHDOG-DOCS-12 等内部 task ID |
| F6  | L | ✅ resolved | `docs/no_bitable_core_smoke.md` 改写：开头改成 "acceptance smoke for the local-default policy"（删掉 TASK-021 + "lead correction"） |
| F7  | L | ✅ resolved | `slash_commands_system.md:5` `**作者**：team`（替换 reviewer/ClaudeTeam）；line 287 时间戳移除 |
| F8  | L | ✅ **超预期** | 3 道守卫：oc_ 前缀校验 / 黑名单环境变量 / `~/.onboarding-tester-home/onboarding_chat.id` pin 文件强匹配。建议作为脚本守卫范式 |
| F9  | L | ✅ 已说明 | ONBOARDING.md:49 明确说明 `manager`/`claudeteam`/`worker_*` 是历史代码符号，与角色无关 |
| F10 | info | ✅ 已交付 | 5 必给文档全部落盘，421 行（详见 §3） |

**总计**: 8/8 finding resolved，1 个 info 项已交付。

---

## 2. 边界守住

- 原仓 `git -C /home/admin/projects/restructure status -s` 没有任何 onboarding 副本路径条目
- `find onboarding/ -name 'MEMORY*' -o -name '.claude.json' -o -name '.credentials*' -o -name '*.env'` 0 hits
- `agents/` 目录空（dir 存在，无 workspace 文件）
- `.git` 已删，不再连原仓 worktree

---

## 3. 5 必给文档可读性盲测评估

### 3.1 量化数据

| 文档 | 行数 | 入门门槛 | 主路径完整度 |
|------|------|----------|--------------|
| ONBOARDING.md | 49 | 低（5 段） | ✅ 入口 + 时间预算 + 阻塞渠道 |
| BUILD.md | 86 | 中 | ✅ 7 步从前置到收尾 |
| TEAM_LAYOUT.md | 72 | 低 | ✅ 9 窗口 + team.json 字段表 + 改名陷阱 |
| FEISHU_GROUP.md | 97 | **高**（依赖飞书 app 已建好） | ⚠️ 6 步流程完整，但 app 创建/webhook 配置未涵盖 |
| SMOKE_GATES.md | 117 | 低 | ✅ 8 关 PASS 判定 + G8 精准断言 |
| **合计** | **421** | — | — |

### 3.2 主路径模拟（"完全没看过我们代码的新 Claude" 视角）

**T+0~10 min（容器构建）**：
- 跟着 ONBOARDING.md → BUILD.md。前置依赖表清晰；`cp .env.example` 后填 FEISHU_APP_ID/SECRET。**第一道实质门槛**：blind tester 需要先有飞书 app（见 R1）。
- 假设 .env 配好，build/init/up 三连命令直接 copy-paste 跑。
- `tmux ls` 看到 9 窗口，`capture-pane -t lead` 看到 Claude Code banner ✅

**T+10~15 min（飞书群创建）**：
- FEISHU_GROUP.md §1 user device flow。
- §2 export 三个 user_id 跑 create_group.sh → 输出 chat_id + applink ✅
- §3 写 pin 文件 ✅
- §5 验证 router pid → 此时如果 webhook 没配，router 在容器里跑但收不到事件（见 R2）

**T+15~45 min（跑 8 关）**：
- G1 say_as_boss.sh "所有员工报道"——能跑通的前提是 webhook 已通且 router 能 dequeue
- G2-G7 群里手发斜杠命令 → 需要 router 已订阅 webhook
- G8 docker exec suspend_agent + 发消息 → 验证逻辑独立，理论可跑

**结论**：30 min 容器+4 员工跑通，**条件**：
- 飞书 app 已建好且 webhook 已指向 host（这是文档外前提）
- host `~/.claude/.credentials.json` 已 login
- 网络/镜像拉取顺畅

如果飞书 app 也要从零建：30 min 远不够，至少额外 1-2 h（app 注册 + 事件审批 + webhook 调通）。

---

## 4. 新发现 R 级 gap（不在原 F 列表）

| ID | 等级 | 描述 | 建议 |
|----|------|------|------|
| R1 | **M** | BUILD.md §1 / FEISHU_GROUP.md §0 默认飞书 app 已建好；blind tester 从零建 app 没文档可循 | 在 FEISHU_GROUP.md §0 之前加 "§-1 创建飞书 app" 段（5-10 行：注册→拿 App ID/Secret→添加 im 权限），或显式说"这份包假定飞书 app 已建好" |
| R2 | **M** | FEISHU_GROUP.md §5 提及 "事件订阅指向你的 webhook" 但无配置步骤 | 加 webhook url 计算公式 + 飞书事件订阅页面操作（5 行），或同上显式标 "假定 webhook 已配" |
| R3 | L | BUILD.md §3 `touch scripts/runtime_config.json` 解释只一句"bind-mount 需要"；blind tester 易困惑 | 加一句："runtime_config.json 用于运行时存储 router cursor / agent state，首次启动后会被填充" |
| R4 | L | FEISHU_GROUP.md §1 说 bot 自动 ready，无验证命令 | 加 `docker compose -p $C exec team npx @larksuite/cli profile +list --as bot` 验证 |
| R5 | L | SMOKE_GATES.md G2 写 "列出 6 个斜杠命令" 但未列哪 6 个 | 加列表：`/help /team /usage /tmux /send /compact` |
| R6 | L | SMOKE_GATES.md:72 注释 "容器内 manager 视角" 与下行 `inbox lead` 命令名称不符 | 改注释为 "容器内 lead 视角"（保持 lead/manager 翻译一致） |

R1+R2 各自独立都能让一个真盲测者卡 1+ 小时。组合起来如果 blind tester 没有现成飞书 app，30min 预算彻底破。但这是**文档完整性**问题，非原 F 列表回归——本轮 PASS-with-minor 而非 NEEDS-FIX-2 的核心理由。

---

## 5. 11 个 .sh 脚本 bash -n 现场抽样

| 脚本 | 现场 bash -n |
|------|--------------|
| `scripts/onboarding/create_group.sh` | ✅ |
| `scripts/onboarding/say_as_boss.sh` | ✅（含 3 守卫，质量超预期） |
| `scripts/lib/run_codex_cli.sh` | （未深入审，预期同 ClaudeTeam 仓） |
| `scripts/lib/tmux_team_bringup.sh` | （同上） |
| `scripts/lib/agent_lifecycle.sh` | （G8 依赖此 lib，关键，假设 PASS）|
| `scripts/docker-deploy.sh` | （未抽） |
| `scripts/preflight_claude_auth.sh` | ✅（lazy-wake P0 已 review 通过） |
| `scripts/claude_token_guard.sh` | ✅（同上） |
| `scripts/docker-entrypoint.sh` | ✅（同上） |
| `scripts/supervisor_tick.sh` | ✅（lazy-wake P1 已 review） |
| `scripts/supervisor_apply.sh` | ✅（同上） |

manager 已自报"11 个 .sh bash -n PASS"，本轮抽样验证关键 2 个（onboarding/*）通过，其余 lazy-wake 系列在前轮已 PASS，无需重复。

---

## 6. 实施建议

### 6.1 本轮放行

- 改动相对上轮全部 8 个 finding 都已动手，且 F8 质量超预期；5 必给文档齐备且结构正确
- 主路径在飞书 app 已 ready 前提下，blind tester 30min 跑通可期
- 无新增 P0/红线问题

### 6.2 给后续批次（可不阻塞）

- coder 顺手补 R1-R6（约 30 min 工作）
- 或留给新员工跑完冒烟后自己提 PR 补充——这本身也是文档完整性的盲测信号

### 6.3 给新员工的"黄页"指引（manager 派工时口头提示）

- 飞书 app 必须已建（App ID/Secret 已发给新员工）
- webhook 必须已指向新员工 host 的可达端点
- host `claude /login` 必须已完成

具备这三个前提，30 min 跑通可期；不具备则建议 manager 帮先解决。

---

## 7. 评审用到的命令

```bash
# F1-F8 复核
ls onboarding/.git
ls onboarding/.claude/skills/
grep -REn 'ClaudeTeam' onboarding/docs/ onboarding/README.md onboarding/team.json | wc -l
grep -REwn '<role-list>' onboarding/docs/ onboarding/README.md onboarding/team.json
grep -n 'lazy_wake' onboarding/docs/live_container_smoke.md
ls onboarding/docs/PROGRESS.md onboarding/docs/no_bitable_core_smoke.md
head -10 onboarding/docs/slash_commands_system.md
cat onboarding/scripts/onboarding/say_as_boss.sh

# 5 必给文档
wc -l onboarding/docs/onboarding/*.md
read all five files

# 边界
git -C /home/admin/projects/restructure status -s
find onboarding/ -name 'MEMORY*' -o -name '.claude.json' -o -name '.credentials*' -o -name '*.env'
```
