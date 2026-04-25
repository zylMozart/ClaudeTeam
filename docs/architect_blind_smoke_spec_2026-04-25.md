# 蒙眼冒烟（Onboarding 验证）方案 spec · 2026-04-25

**作者**：architect
**目标**：用一个**全新 Claude Code session**（不知道现有命名/历史）按一份**脱敏 onboarding 包**独立跑通容器搭建 + 4 员工 + 飞书群 + 全套冒烟。能跑通 = onboarding 真的可用；跑不通 = 补文档。
**最终交付 + 老板亲测入口**：可加入的飞书群 applink + 群里能模拟老板身份发消息。
**ETA 输出**：本 spec 10 分钟内交付；新员工实跑预计 1-3 小时。

---

## 0. TL;DR

| 项                       | 决策                                                                                       |
|--------------------------|--------------------------------------------------------------------------------------------|
| 副本路径                 | `/home/admin/projects/restructure-onboarding/`（rsync 拷，排除 .git/state/cache/凭证目录） |
| 全删范围                 | 副本独占的 docker container/image/volume + 副本工作区状态；**不动**原仓任何东西            |
| 新员工 sandbox            | host 新建 tmux session `onboarding-tester`；`HOME=/home/admin/onboarding-tester-home` 隔离 |
| 命名脱敏                 | manager→`lead`，worker_cc/codex/kimi/gemini→`agent_a/b/c/d`，qa_smoke→`tester`             |
| 飞书群                   | bot 创建 + 拉新员工 user_id + bot；返回 chat_id → 拼 applink 给老板                       |
| 模拟老板                 | 复用 reference_lark_cli_user_token.md 的 device flow + `--as user` 发消息                  |
| 冒烟覆盖                 | G1 全员应答 + G2-G7 六斜杠 + G8 lazy-wake（共 8 关）                                       |
| 验收                     | 新员工独立跑通（不问 manager / 不读内部 memory），全 8 关 PASS                              |
| 沉淀文档                 | `docs/smoke_test_runbook_v3.md`（参数化，可重复，由新员工实跑后定稿）                      |

---

## 1. 副本路径与复制方式

### 1.1 路径

`/home/admin/projects/restructure-onboarding/`

### 1.2 复制命令

```bash
# 在 host 上跑，绝不动原 /home/admin/projects/restructure/
rsync -aH --info=progress2 \
  --exclude '.git/' \
  --exclude '.git_old/' \
  --exclude 'state/' \
  --exclude '.kimi-credentials/' \
  --exclude '.codex-credentials/' \
  --exclude '.gemini-credentials/' \
  --exclude '.lark-cli-credentials/' \
  --exclude '.claude-credentials/' \
  --exclude 'agents/*/workspace/' \
  --exclude 'workspace/' \
  --exclude '__pycache__/' \
  --exclude '*.pyc' \
  --exclude 'node_modules/' \
  --exclude '.venv/' \
  --exclude 'venv/' \
  --exclude 'docs/architect_*' \
  --exclude 'docs/coder_*' \
  --exclude 'docs/qa_*' \
  --exclude 'docs/lazy_wake_resume_smoke_*' \
  --exclude 'docs/team_slash_status_design.md' \
  --exclude 'docs/boss_todo_bitable_design.md' \
  --exclude 'docs/deployment_repair_backlog_*' \
  --exclude 'agents/*/identity.md' \
  --exclude 'agents/*/core_memory.md' \
  --exclude '.tmux_intercept.log' \
  /home/admin/projects/restructure/ \
  /home/admin/projects/restructure-onboarding/
```

### 1.3 排除项的理由

- `.git/`：避免新员工偷看历史。如果想保留可控的 git 历史，单独 `git init` 后做一次"clean tree"提交。
- `state/`、`*-credentials/`、`agents/*/workspace/`、`workspace/`：所有运行时副产物
- `docs/architect_*`、`docs/coder_*`、`docs/qa_*`、`docs/team_slash_status_design.md`、`docs/lazy_wake_*`、`docs/deployment_repair_*`：内部设计/复盘/incident 记录（脱敏要求）
- `agents/*/identity.md`、`agents/*/core_memory.md`：现有员工身份和记忆
- `__pycache__/`、`*.pyc`、`node_modules/`、`.venv/`：构建产物

### 1.4 副本里需要**保留**的关键文件

- `Dockerfile` / `docker-compose.yml` / `docker-compose.live-smoke.override.yml`
- `scripts/`（全部脚本，含 entrypoint、agent_lifecycle、token_guard、preflight、supervisor_tick 等）
- `src/claudeteam/`（生产代码）
- `tests/`（回归测试）
- `docs/README.md`、`docs/README_CN.md`、`docs/ARCHITECTURE.md`、`docs/CONTRIBUTING.md`、`docs/CODE_STYLE.md`、`docs/TROUBLESHOOTING.md`、`docs/DEVELOPMENT.md`、`docs/OPERATIONS.md`、`docs/POLICY.md`、`docs/public_contracts.md`、`docs/live_container_smoke.md`（脱敏后；**移除**那些含 manager/worker_cc 等具体名字的章节）
- `docs/standard_skill_catalog.md`、`docs/toolchain_skill_restructure.md`、`docs/slash_commands_system.md`、`docs/message_rendering_spec.md`
- `team.json`（**改成中性命名**，见 §4.1）

### 1.5 二次脱敏（rsync 后做一遍 grep + 替换）

```bash
cd /home/admin/projects/restructure-onboarding
# 列出还残留的内部命名
grep -RIEn "manager|worker_cc|worker_codex|worker_kimi|worker_gemini|qa_smoke|architect|toolsmith|coder|docs_keeper" docs/ team.json README.md README_CN.md 2>/dev/null
# 按 §4.1 命名表替换；保留代码/脚本里非用户可见的 default 不动
```

具体替换规则在 §4.1 命名表。

---

## 2. 全删清单（仅作用于副本/新员工，绝不动任何现有运行容器）

### 2.1 Docker 资源（**用 project name 隔离**，避免误伤）

新员工启动副本时**必须**用独立 `COMPOSE_PROJECT_NAME=onboarding-blind`，所有 nuke 都按 project 过滤：

```bash
# 在副本目录里跑
cd /home/admin/projects/restructure-onboarding
export COMPOSE_PROJECT_NAME=onboarding-blind

# 1. 容器
docker compose -p onboarding-blind down -v --remove-orphans 2>/dev/null || true
docker ps -a --filter label=com.docker.compose.project=onboarding-blind -q | xargs -r docker rm -f

# 2. image（可选；首次完全干净跑 build 用）
docker images --filter reference="onboarding-blind*" -q | xargs -r docker rmi -f

# 3. volume
docker volume ls --filter label=com.docker.compose.project=onboarding-blind -q | xargs -r docker volume rm
```

### 2.2 工作区状态

```bash
# 副本独占的状态/凭证目录全部清空（rsync 已排除部分，此处再一遍兜底）
rm -rf state/ workspace/ \
       agents/*/workspace/ \
       .kimi-credentials/ .codex-credentials/ .gemini-credentials/ \
       .lark-cli-credentials/ .claude-credentials/
```

### 2.3 严禁触碰（黑名单）

- ❌ `/home/admin/projects/restructure/`（原仓）
- ❌ host tmux server-manager session（team01 在跑）
- ❌ docker container/image/volume **只要 project name 不是 onboarding-blind 一律不动**
- ❌ `claudeteam-restructure-team-prod-hardened-1`、`maintenance-kimi-*`、`life-pm-*`、`team02-*` 等已知容器（实施前 `docker ps -a` 确认）
- ❌ 老板的飞书主群（main feishu group）
- ❌ host `~/.claude/`（老板的 OAuth 凭证不要碰；新员工有自己的 sandbox HOME，§3）

---

## 3. 新员工沙盒

### 3.1 tmux session

```bash
# host 上新建 session（隔离于 server-manager 主 session）
tmux new-session -d -s onboarding-tester -c /home/admin/projects/restructure-onboarding
```

session 名固定为 `onboarding-tester`，避免和现有 `server-manager` / `restructure` 冲突。

### 3.2 隔离 HOME（新员工独立的 claude/lark-cli 凭证空间）

新员工的 HOME 不复用 host admin 的 `~/.claude/`，否则相当于"知情人玩"。设置：

```bash
mkdir -p /home/admin/onboarding-tester-home/{.claude,.lark-cli,.local/share/lark-cli}
chmod 700 /home/admin/onboarding-tester-home
```

新员工所有命令前缀**必须**用：

```bash
HOME=/home/admin/onboarding-tester-home <cmd>
```

或在新员工 tmux session 里 `export HOME=/home/admin/onboarding-tester-home` 后续所有 shell 自动继承。

### 3.3 fresh Claude Code session

新员工自己跑 `claude` 第一次，会触发 `claude /login` device flow（需要新 OAuth 账号或老板提供测试账号）。**绝不**复制 `/home/admin/.claude/.credentials.json` 进新员工 HOME。

> 注：若老板希望复用同一个 Claude 账号节省 device flow 次数，可在 onboarding 包里写"用 ANTHROPIC_API_KEY 模式"，让新员工把 key 填进 `.env`。这是**唯一**允许的"凭证复用"快捷路径，且不暴露 OAuth refresh_token。

### 3.4 网络/group 隔离

新员工创建的飞书群是**独立测试群**（§5），不会和老板主群产生交叉。bot 用同一个 app（restructure 测试 bot），但 chat_id 不同。

---

## 4. Onboarding 包内容（脱敏）

### 4.1 命名映射

| 现有内部名     | 蒙眼包中性名 | 角色定位                              |
|----------------|--------------|---------------------------------------|
| manager        | `lead`       | 团队主管（接老板消息、派任务）         |
| worker_cc      | `agent_a`    | claude-code CLI 员工                  |
| worker_codex   | `agent_b`    | codex-cli CLI 员工                    |
| worker_kimi    | `agent_c`    | kimi-cli CLI 员工                     |
| worker_gemini  | `agent_d`    | gemini-cli CLI 员工                   |
| qa_smoke       | `tester`     | 冒烟员工（可与新员工本人合一）         |
| architect      | （不入团队） | 仅作 onboarding 文档作者署名（脱敏）   |

### 4.2 onboarding 包**必给**的文档（5 份，全脱敏）

新员工拿到的 zip / 目录里只有这些：

| # | 文件                                                | 内容                                                                                      |
|---|-----------------------------------------------------|-------------------------------------------------------------------------------------------|
| 1 | `ONBOARDING.md`                                     | 总入口：欢迎 + 目标 + 时间预算 + 必读顺序 + 求助渠道                                       |
| 2 | `BUILD.md`                                          | Dockerfile/compose 用法、`docker compose -p onboarding-blind up -d --build` 最小启动      |
| 3 | `TEAM_LAYOUT.md`                                    | tmux 命名规则（lead/agent_a-d/router/kanban/watchdog/supervisor_ticker）+ team.json 示例  |
| 4 | `SMOKE_GATES.md`                                    | 8 关验证矩阵（§7），每关 PASS 判定 + 复现命令                                              |
| 5 | `FEISHU_GROUP.md`                                   | 创建测试群 + 把老板/bot/新员工 拉进去 + applink 拼装命令（§5）                            |

加一份脱敏后的可读参考文档（**不能**含内部 memory）：

| # | 来源                          | 脱敏后                                       |
|---|-------------------------------|----------------------------------------------|
| 6 | `docs/README_CN.md`           | 替换全部内部命名后保留                       |
| 7 | `docs/ARCHITECTURE.md`        | 替换命名 + 删除"过往 incident"段落           |
| 8 | `docs/TROUBLESHOOTING.md`     | 保留通用故障；删除特定 incident 时间戳       |
| 9 | `docs/slash_commands_system.md` | 保留斜杠命令清单，删除内部命名实例           |

### 4.3 onboarding 包**严禁**给的内容

- ❌ `MEMORY.md` 及 `~/.claude/projects/.../memory/` 任何内部记忆
- ❌ 本团队 cheat sheet / 内部 wiki
- ❌ `docs/architect_*`、`docs/coder_*`、`docs/qa_*`、`docs/lazy_wake_*`、`docs/deployment_repair_*`
- ❌ 提及 worker_cc/manager/qa_smoke/architect 的 PR 描述、commit message
- ❌ 之前任何冒烟报告（`docs/smoke_test_runbook_v3.md` 由新员工跑完后**自己**定稿）

### 4.4 ONBOARDING.md 大纲（给 coder 落盘时照写）

```markdown
# Onboarding · 团队搭建实操

## 你的目标
1 小时内跑通：1 主管 + 4 员工 容器；测试群创建并发出 applink；冒烟 8 关全 PASS。

## 必读顺序
BUILD.md → TEAM_LAYOUT.md → FEISHU_GROUP.md → SMOKE_GATES.md

## 阻塞时
仅以下渠道（按优先序）：
1. Anthropic / lark / docker 官方文档
2. SMOKE_GATES.md 末尾的 FAQ
3. **不可以**问任何已存在的团队成员或读他们的 memory

## 完工标准
飞书 applink 发到 boss → boss 进群发"所有员工报道" → 4 员工各回一条 → 8 关 PASS → 你把过程写进 docs/smoke_test_runbook_v3.md
```

---

## 5. 飞书群创建 + applink 拼装

### 5.1 创建群（bot 身份）

参考 `reference_lark_docs_create.md` 同款 lark-cli 路径：

```bash
# 用 bot token 创建独立测试群
npx @larksuite/cli im +chat-create --as bot --data '{
  "name": "onboarding-blind-smoke",
  "description": "blind onboarding smoke 2026-04-25",
  "user_id_list": ["<boss_user_id>", "<new_tester_user_id>"]
}' | tee /tmp/onboarding_chat.json

CHAT_ID=$(jq -r '.data.chat_id' /tmp/onboarding_chat.json)
```

### 5.2 把 bot 也加进群（如果创建时没自动加）

```bash
npx @larksuite/cli im +create-chat-members --as bot \
  --params "chat_id=${CHAT_ID}&member_id_type=app_id" \
  --data '{"id_list": ["<bot_app_id>"]}'
```

### 5.3 applink 拼装

飞书群 applink 格式：

```text
https://applink.feishu.cn/client/chat/chatter/add_by_link?token=<chat_token>
```

`chat_token` 从 `im +get-chat-link` 拉：

```bash
npx @larksuite/cli im +get-chat-link --as bot --params "chat_id=${CHAT_ID}" \
  | jq -r '.data.share_link'
```

把这个 share_link 直接发给老板，老板点击进群。

### 5.4 不要做的

- 不能把测试群名字命名为含敏感词（避免和老板主群混淆）
- 不能让 bot 在创建后立刻在群里发 hello，等冒烟脚本触发再发

---

## 6. 模拟老板发消息

复用 `reference_lark_cli_user_token.md` 的路径：

```bash
# 1. 一次性 device flow（新员工自己 host 浏览器走）
npx @larksuite/cli login --as user

# 2. 把 user_access_token 缓存到隔离 HOME
ls $HOME/.local/share/lark-cli/   # 应有 master.key + token cache

# 3. 用 user 身份发消息（伪装老板视角）
npx @larksuite/cli im +create-chat-message --as user \
  --params "receive_id_type=chat_id" \
  --data "{
    \"receive_id\": \"${CHAT_ID}\",
    \"msg_type\": \"text\",
    \"content\": \"{\\\"text\\\":\\\"所有员工报道\\\"}\"
  }"
```

封装成 `scripts/onboarding/say_as_boss.sh`：

```bash
#!/usr/bin/env bash
# 用法：bash scripts/onboarding/say_as_boss.sh "<message>"
CHAT_ID="${ONBOARDING_CHAT_ID:?need ONBOARDING_CHAT_ID}"
TEXT="${1:?need text}"
npx @larksuite/cli im +create-chat-message --as user \
  --params "receive_id_type=chat_id" \
  --data "$(jq -nc --arg id "$CHAT_ID" --arg t "$TEXT" \
       '{receive_id:$id, msg_type:"text", content: ($t|{text:.}|tostring)}')"
```

---

## 7. 冒烟覆盖矩阵（8 关）

| 关 | 名称              | 触发                                                           | PASS 判定                                                                                  |
|----|-------------------|----------------------------------------------------------------|--------------------------------------------------------------------------------------------|
| G1 | 全员应答          | boss 身份在群里发"所有员工报道"                                 | 90s 内 4 个 agent 各发出一条 `say` 消息（自报名 + CLI 类型）                               |
| G2 | `/help`           | 群里发 `/help`                                                 | 卡片返回，列出 6 个斜杠命令                                                                 |
| G3 | `/team`           | 群里发 `/team`                                                 | 卡片：5 行（lead + 4 agent），每行含 状态 + CLI                                            |
| G4 | `/usage`          | 群里发 `/usage`                                                | 卡片：周配额 + 各 CLI Extra usage 快照                                                      |
| G5 | `/tmux`           | 群里发 `/tmux`                                                 | 卡片：tmux 窗口列表（含 lead + agent_a-d + router + kanban + watchdog）                    |
| G6 | `/send`           | 群里发 `/send agent_a hello`                                   | agent_a inbox 多一条 hello；卡片回投递确认                                                  |
| G7 | `/compact`        | 群里发 `/compact`                                              | 每 agent 收到 compaction ack                                                                |
| G8 | lazy-wake 唤醒    | 手动 `suspend_agent agent_a` → boss 发消息给 agent_a            | agent_a 从 💤 banner 苏醒；新 claude pid 的 cmdline 含 `--resume <uuid>`；agent_a 正常回应 |

**任意一关 FAIL** = onboarding 文档有缺陷，记录到"补文档清单"。

### 7.1 G8 的精准断言

```bash
# 手动 suspend
docker exec <C> bash -c 'source /app/scripts/lib/agent_lifecycle.sh && suspend_agent agent_a'

# boss 身份发消息（§6 脚本）
bash scripts/onboarding/say_as_boss.sh "agent_a 你好，回个话"

# ≤30s 内
docker exec <C> ps -eo pid,cmd | grep -E "claude.*--resume" | grep agent_a
# 必须见到 --resume <uuid>
docker exec <C> jq .agent_a /app/scripts/.agent_sessions.json
# 必须有 uuid，且 == 上一步 cmdline 中的 uuid
```

---

## 8. 验收准则

| # | 准则                                                                                          |
|---|-----------------------------------------------------------------------------------------------|
| 1 | 新员工**不**问任何现有团队成员（lead/agent/tester/architect/coder/manager），不读其 memory     |
| 2 | 允许读：自己 onboarding 包；Anthropic/lark/docker 官方文档；stackoverflow                     |
| 3 | 8 关全 PASS                                                                                    |
| 4 | 新员工把过程定稿到 `docs/smoke_test_runbook_v3.md`（参数化、可重复、不含本次 chat_id 等敏感值） |
| 5 | applink 老板进群亲见 4 员工各发 1 条                                                          |
| 6 | 副本路径与原仓 `git diff` 范围仅在新生成文件（runbook v3、state、workspace）                  |

---

## 9. `docs/smoke_test_runbook_v3.md` 沉淀模板（新员工照填）

```markdown
# Smoke Test Runbook v3 · {{DATE}}

## 0. 适用范围
此 runbook 假定已克隆好仓库且具备 docker / lark-cli / claude CLI。

## 1. 参数化变量
| 变量                    | 说明                            | 示例                                       |
|-------------------------|---------------------------------|--------------------------------------------|
| COMPOSE_PROJECT_NAME    | docker project 名               | onboarding-blind / smoke-{{DATE}}          |
| ONBOARDING_CHAT_ID      | 测试群 chat_id                  | oc_xxx                                     |
| LARK_USER_NAME          | 新员工自己的飞书 user open_id  | ou_xxx                                     |
| BOSS_USER_ID            | 老板 user_id                   | ou_xxx                                     |

## 2. 启动序（≈10min）
（新员工填实际命令，去除 prod-hardened；用 dev compose）

## 3. 创建测试群（≈3min）
（贴 §5 的命令模板）

## 4. 执行 8 关（≈30min）
（贴 §7 表 + 每关用过的实际命令 + 截图/output）

## 5. 阻塞与解决
（新员工自己遇到的真坑 + 解决方案）

## 6. 验收 checklist
[ ] G1 全员应答
[ ] G2 /help
... (照 §7)
```

新员工跑通后**只**保留此模板的填写版作为沉淀，把"具体 chat_id / token / 用户 id"替换成 `{{XXX}}` 占位符，下次任何 manager 拿来参数化即跑。

---

## 10. 风险与边界（红线）

| 红线                                                              | 触发条件                          | 立即停手 + 上报                  |
|-------------------------------------------------------------------|-----------------------------------|----------------------------------|
| 误改 `/home/admin/projects/restructure/`                          | rsync 反向 / cd 错路径             | 任意写操作                       |
| 误删非 onboarding-blind 项目的容器                                 | docker rm 不带 project 过滤        | 任意 docker rm                   |
| 误删 host `~/.claude/`                                             | 写命令含 `$HOME/.claude`           | 任意写                           |
| 误进老板主群                                                       | applink 错给主群                   | 群名审核                         |
| 暴露老板 OAuth refresh_token / lark master.key                     | 把 host 凭证 cp 进 onboarding HOME | 任意 cp ~/.claude / ~/.lark-cli  |
| 触碰 server-manager / maintenance-kimi / life-pm / team02 任何资源 | tmux session 名错 / docker label 错 | 任意操作                         |

**安全网**：所有 docker 操作必须带 `-p onboarding-blind` 或 `--filter label=com.docker.compose.project=onboarding-blind`；所有 host 路径写操作必须以 `/home/admin/projects/restructure-onboarding/` 或 `/home/admin/onboarding-tester-home/` 开头。脚本入口加 grep 守卫拒绝其他前缀。

---

## 11. 给 manager 的实施清单

| # | 动作                                                                  | 负责人          |
|---|-----------------------------------------------------------------------|-----------------|
| 1 | 按 §1 复制副本 + §2.2 清状态                                          | manager 或 coder |
| 2 | 按 §4 脱敏：替换命名 + 删除内部 docs                                   | coder           |
| 3 | 按 §5/§6 写 `scripts/onboarding/{create_group.sh, say_as_boss.sh}`    | coder           |
| 4 | 按 §3 起 host tmux `onboarding-tester` + 隔离 HOME                    | manager         |
| 5 | 招新员工（host 上跑一份新 claude session）；交付 onboarding 包        | manager         |
| 6 | 新员工独立跑 → 出 `docs/smoke_test_runbook_v3.md`                      | 新员工          |
| 7 | 8 关全 PASS → 老板亲测 applink → manager 验收                         | manager + 老板  |
| 8 | 跑不通的关补文档：哪一关卡住了 → onboarding 包对应章节加 FAQ          | manager → 修文档 |

---

*architect · 10 分钟出稿 · 2026-04-25*
*下一跳：manager 决定何时启动；coder 按 §11 第 2-3 项动手；新员工招募中。*
