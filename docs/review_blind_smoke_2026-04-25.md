# Review · blind smoke onboarding 包 §11 #1-#3 实施

- 日期: 2026-04-25
- 评审人: toolsmith
- 上游 spec: `docs/architect_blind_smoke_spec_2026-04-25.md`
- 副本: `/home/admin/projects/restructure-onboarding/`（rsync 副本，原仓零触碰）
- coder 范围: §11 #1（复制 + 清状态）/ #2（脱敏）/ #3（onboarding 脚本）

---

## 0. Verdict

**NEEDS-FIX**

裸数据：原仓 0 触碰 ✅；agent 角色名（manager/qa_smoke/architect/toolsmith/coder/docs_keeper/worker_*）在 docs/team.json/README 用 `-w` 词边界 grep **0 命中** ✅；spec §4.3 全部 forbidden 文件前缀（architect_*/coder_*/qa_*/lazy_wake_*/deployment_repair_*/review_*）已删 ✅；2 个 onboarding 脚本 §5/§6 模板对齐 ✅。

但发现 4 个 **M 级**问题会让 blind 测试者直接看到团队身份/机制，实质破坏"盲测"目标，必须修；外加 3 个 L 级 + 1 个 info（§4.2 五份必给文档延后）。

**建议**：M 级 4 项 30-60 分钟可修完；修完转 PASS-with-minor 即可 qa_smoke。

---

## 1. Findings 总表

| ID  | 等级  | 范畴       | 描述                                                                                       | 建议动作                                                              |
|-----|-------|------------|--------------------------------------------------------------------------------------------|-----------------------------------------------------------------------|
| F1  | **M** | 原仓污染   | `.git` 文件被 rsync 带过来，仍指向原仓 worktree gitdir                                      | `rm /home/admin/projects/restructure-onboarding/.git` 或 `git init` 起一个干净 repo |
| F2  | **M** | 身份脱敏   | 团队代号 `ClaudeTeam` 在 docs/ 出现 **151 次**，blind 测试者一眼能看出是哪支团队           | docs/ 全局替换 `ClaudeTeam` → 中性名（如 `the team` / `Project X`）   |
| F3  | **M** | 内部 ref   | `docs/live_container_smoke.md:226` 引用已删除的 `docs/lazy_wake_resume_smoke_2026-04-25.md` | 删该行/段，或替换为通用描述；同时清理 §4.3 forbidden 文件名引用       |
| F4  | **M** | 团队 cheat | `.claude/skills/{hire,fire,team-communication,server-status,rate-limit-triage}` 是团队内部 cheat sheet | 这 5 个 skill 目录从 onboarding 副本移除（保留 hooks/ 配合斜杠运行即可） |
| F5  | L     | 内部上下文 | `docs/PROGRESS.md` 是重构进展总表（WATCHDOG-DOCS-12 等内部任务 ID）                         | 删除或重写为新员工角度的"项目阶段简介"                                |
| F6  | L     | 内部上下文 | `docs/no_bitable_core_smoke.md` 含 `TASK-021` + "lead correction"；定位过时              | 确认是否当前路线，否则删；保留则前置一段说明这是历史背景              |
| F7  | L     | 时间戳泄漏 | `docs/slash_commands_system.md:5/287` 含 "reviewer（ClaudeTeam）2026-04-20 03:50–04:30"    | 改为通用 "designed by team" + 抹掉具体时间                            |
| F8  | L     | 守卫缺失   | `scripts/onboarding/say_as_boss.sh` 仅 HUMAN comment 红线，无对 chat_id 的程序化校验       | 加 `case "$ONBOARDING_CHAT_ID" in oc_*) ... esac` 或拒绝列表（见 §3.2）|
| F9  | L     | 代码引用   | docs/ 含 259 处代码符号子串（`_send_manager_alert`/`memory_manager.py` 等），manager 字面 | 与 spec 一致（代码 default 可保留），但建议在 ONBOARDING.md 加说明：`manager` 是历史代码符号，非团队角色 |
| F10 | info  | 待补       | spec §4.2 五份必给文档（ONBOARDING/BUILD/TEAM_LAYOUT/SMOKE_GATES/FEISHU_GROUP）未交付      | coder 已声明留给后续批次；不阻塞本轮，但要在 manager 计划里排期       |

---

## 2. 脱敏审计详情

### 2.1 词边界 grep（agent 角色名严格命中）

```bash
grep -REwn 'manager|worker_cc|worker_codex|worker_kimi|worker_gemini|qa_smoke|architect|toolsmith|coder|docs_keeper' \
  restructure-onboarding/docs/ restructure-onboarding/team.json restructure-onboarding/README.md
# → 0 hits ✅
```

`team.json` 已改为 `lead/agent_a/agent_b/agent_c/agent_d` ✅。

### 2.2 子串 grep（含 architecture / _send_manager_alert）

不带 `-w`：docs 命中很多，但全部是

- `architecture/architectural` —— 软件架构英语词，与 agent 角色 `architect` 无关
- `_send_manager_alert` / `notify_manager` / `memory_manager.py` —— Python 代码符号字面，docs 在描述这些函数

**结论**：spec 允许"代码内部 default 可保留"。docs 描述代码符号是其延伸，可接受 → 列入 F9 备案，不影响主结论。

### 2.3 forbidden 文件前缀（spec §4.3）

```bash
ls onboarding/docs/ | grep -E '^(architect|coder|qa|lazy_wake|deployment_repair|review)'
# → 0 results ✅
```

但 `live_container_smoke.md:226` 仍**文本**引用 `lazy_wake_resume_smoke_2026-04-25.md`（即使文件已删）→ F3。

### 2.4 ClaudeTeam 代号

```bash
grep -REin 'ClaudeTeam' onboarding/docs/
# → 151 hits across many files (ARCHITECTURE/ROADMAP/TESTING/... ) → F2
```

不在 spec §4.1 命名表里，但 blind 测试者直接看到团队代号，"盲"假设破。

---

## 3. 红线检查

### 3.1 凭证 / 状态 / memory（spec §10）

| 红线项                                                | 命中   | 备注                                  |
|-------------------------------------------------------|--------|---------------------------------------|
| host `~/.claude` / `~/.lark-cli` 凭证副本              | ❌ 无 | `find -name '.claude.json'/'.credentials*'/'.env'` 全 0 |
| 原仓 `.git` 痕迹                                       | ⚠️ **F1** | `.git` 单文件指针仍在，gitdir 指向 ClaudeTeam worktree |
| 原仓 `state/` 痕迹                                     | ❌ 无 | （rsync 应已排除）                  |
| `agents/` workspace 痕迹                              | ❌ 无 | dir 存在但为空                       |
| `MEMORY.md`                                           | ❌ 无 | `find -name 'MEMORY*'` 0 hits        |
| 团队 cheat sheet                                       | ⚠️ **F4** | `.claude/skills/{hire,fire,team-communication,server-status}` |

### 3.2 say_as_boss.sh chat_id 守卫建议

当前仅注释红线（被人忽略概率高）。建议：

```bash
# 拒绝可能的主群 chat_id（手动维护一个黑名单）
DENYLIST=("oc_主群id1" "oc_主群id2")
for d in "${DENYLIST[@]}"; do
  [[ "$ONBOARDING_CHAT_ID" == "$d" ]] && {
    echo "❌ 拒绝向主群发消息: $ONBOARDING_CHAT_ID" >&2
    exit 9
  }
done
```

或更软：要求 `ONBOARDING_CHAT_ID` 必须等于 `~/.onboarding-tester-home/onboarding_chat.id` 文件内容，否则拒绝（强制走 create_group.sh 的输出）。

---

## 4. 脚本审计

### 4.1 `scripts/onboarding/create_group.sh`（2594B）

| 项                          | 评估 |
|-----------------------------|------|
| `set -euo pipefail`         | ✅   |
| `chat_id`/`token` 不硬编码  | ✅（全部 env 注入） |
| `--as bot`（spec §5）       | ✅   |
| 必填 env 提示清晰           | ✅（`:?need ...`） |
| `bash -n` 通过              | ✅（coder 已自报） |
| 失败显式 exit               | ✅（exit 1/2 区分） |
| `[2/3]` bot 入群失败仅警告  | ✅（合理 — 创建时可能已自动入群） |

**结论**：PASS。

### 4.2 `scripts/onboarding/say_as_boss.sh`（1458B）

| 项                          | 评估 |
|-----------------------------|------|
| `set -euo pipefail`         | ✅   |
| `--as user`（spec §6）      | ✅   |
| `ONBOARDING_CHAT_ID` 必填   | ✅   |
| `bash -n` 通过              | ✅   |
| chat_id 守卫                | ⚠️ **F8**（仅注释，无程序化） |

**结论**：PASS-with-minor（F8）。

---

## 5. docs/ 残余审计（spec §4.2/§4.3）

副本 `docs/` 现 21 个 .md 文件 + `adrs/` + `media/`。逐项判定：

| 文件                                | 状态 | 评估                                                                          |
|-------------------------------------|------|-------------------------------------------------------------------------------|
| `README.md`                         | 保留 | OK，但含 `architecture` 多次（false positive）                                |
| `README_CN.md`                      | 保留 | spec §4.2 表 #6，OK                                                            |
| `ARCHITECTURE.md`                   | 保留 | spec §4.2 表 #7，OK；但 ClaudeTeam 多次（F2）                                  |
| `OPERATIONS.md`                     | 保留 | OK                                                                             |
| `DEVELOPMENT.md`                    | 保留 | OK                                                                             |
| `TESTING.md`                        | 保留 | OK                                                                             |
| `TROUBLESHOOTING.md`                | 保留 | spec §4.2 表 #8，OK                                                            |
| `slash_commands_system.md`          | 保留 | spec §4.2 表 #9 — F7（reviewer + ClaudeTeam 时间戳）                           |
| `live_container_smoke.md`           | 保留 | **F3**（lazy_wake ref）；含 `2026-04-24/25` 时间戳，spec §4.2 不要求保留      |
| `no_bitable_core_smoke.md`          | 保留 | **F6**（TASK-021/lead correction 内部上下文）                                  |
| `hardening_profile.md`              | 保留 | OK（profile 概念性描述）                                                       |
| `PROGRESS.md`                       | 保留 | **F5**（重构进展总表，内部任务 ID）                                            |
| `ROADMAP.md`                        | 保留 | OK，但 ClaudeTeam 多次（F2）                                                   |
| `POLICY.md` / `CONTRIBUTING.md`     | 保留 | OK（团队风格）                                                                 |
| `CODE_STYLE.md`                     | 保留 | OK                                                                             |
| `message_rendering_spec.md`         | 保留 | OK（产品规格）                                                                 |
| `public_contracts.md`               | 保留 | OK                                                                             |
| `standard_skill_catalog.md`         | 保留 | OK                                                                             |
| `toolchain_skill_restructure.md`    | 保留 | OK                                                                             |
| `adrs/*`                            | 保留 | OK                                                                             |
| `media/*`                           | 保留 | （未深入审，假定无嵌入式截图泄漏；可后续抽样） |

---

## 6. spec §4.2 必给文档（待补 / coder 后续批次）

| #  | 文件               | 当前 | 备注 |
|----|--------------------|------|------|
| 1  | `ONBOARDING.md`    | ❌   | spec §4.4 已给大纲，复制照写即可 |
| 2  | `BUILD.md`         | ❌   | 抄 docker-compose.yml 用法 + `docker compose -p onboarding-blind up -d --build` |
| 3  | `TEAM_LAYOUT.md`   | ❌   | tmux 命名 + team.json 示例（已在副本里） |
| 4  | `SMOKE_GATES.md`   | ❌   | 抄 spec §7 8 关矩阵 + §7.1 G8 精准断言 |
| 5  | `FEISHU_GROUP.md`  | ❌   | 抄 spec §5 + 引用 create_group.sh 输出 |

5/5 缺失。spec §4.2 表头明写"加一份脱敏后的可读参考文档"——必给文档与脱敏文档**两类**。当前 coder 只交了脱敏，未交必给文档。

manager brief 已声明"留给后续批次，不算 P0 阻塞"，但需明确：**没有这 5 份，新员工进来无入口**——qa_smoke 跑不动。建议下一批 coder 立刻接续。

---

## 7. 实施清单（建议给 coder 的 fix 单）

1. `rm /home/admin/projects/restructure-onboarding/.git`（30s，F1）
2. docs/ 全局 `ClaudeTeam` → `Project X`（或商定的中性名）（10min，F2）
3. `live_container_smoke.md:226` 删该行或替换（30s，F3）
4. `rm -r .claude/skills/{hire,fire,team-communication,server-status,rate-limit-triage}`（30s，F4）
5. 删 `docs/PROGRESS.md`（或重写为"项目阶段简介"）（5min，F5）
6. 决定 `no_bitable_core_smoke.md` 删/留（与 architect 确认）（2min，F6）
7. `slash_commands_system.md:5/287` 抹时间戳与"reviewer（ClaudeTeam）"（1min，F7）
8. `say_as_boss.sh` 加 chat_id 守卫（5min，F8）
9. 在 ONBOARDING.md（一旦 §4.2 #1 落盘）首段加"代码层 `manager` 字面是历史符号"说明（F9）

预计总时长 25-30min。改完转 PASS-with-minor，可放 qa_smoke。

---

## 8. 边界确认（原仓零触碰）

```bash
git -C /home/admin/projects/restructure status -s
# 与本任务无关，仅含 creds v2 + lazy-wake + 既有 untracked，未见 onboarding 副本任何改动
```

✅ §10 红线一项守住（"误改 /home/admin/projects/restructure/"）。

---

## 9. 评审用到的命令

```bash
# 副本布局
ls -la /home/admin/projects/restructure-onboarding/
ls    /home/admin/projects/restructure-onboarding/docs/
cat   /home/admin/projects/restructure-onboarding/.git
cat   /home/admin/projects/restructure-onboarding/team.json

# 脱敏（词边界）
grep -REwn '<role-list>' onboarding/docs onboarding/team.json onboarding/README.md
grep -REin 'ClaudeTeam' onboarding/docs/

# forbidden 前缀
ls onboarding/docs/ | grep -E '^(architect|coder|qa|lazy_wake|deployment_repair|review)'

# 凭证
find onboarding/ -name 'MEMORY*' -o -name '.claude.json' -o -name '.credentials*' -o -name '*.env'

# 脚本
bash -n onboarding/scripts/onboarding/{create_group,say_as_boss}.sh
```
