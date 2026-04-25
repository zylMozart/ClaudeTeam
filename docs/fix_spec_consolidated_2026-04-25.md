# Architect · 19 Finding 综合修复 Spec · #60

- **Author**: architect
- **Date**: 2026-04-25
- **Input**:
  - 主报告 `/home/admin/projects/server_manager/ClaudeTeam/docs/qa_blind_smoke_consolidated_2026-04-25.md` (19 finding 分级版)
  - 辅助 `qa_blind_smoke_2026-04-25.md` (阉割版+applink) / `qa_blind_smoke_g1g8_2026-04-25.md` (G1-G8 真冒烟)
  - 老板 4 条新指令（命名收回 / bitable 撤回 / 真从头冒烟 / devops Opus 4.7）
- **Target output**: 全 19 finding 修复 → blind tester 拿包 1h 内 G1-G8 全 PASS 不需 manager 介入
- **Cost budget for coder**: 3 batch（≤3h coder 总工作量）
- **Hot-update 优先**：能 `docker cp` 就不 rebuild；只有 Dockerfile/build-arg 改动才 rebuild

---

## 0 · TL;DR

19 finding（8 P0 / 1 P1 / 4 P2 / 6 P3）按 4 个修复轴归类：

| 轴 | 解决的 finding | batch |
|----|---------------|-------|
| **A · 命名 + 路由 + lazy 三件套** | F-LEAD-NOLAZY · F-G8-1 · F-LAZY-1 · F-G3-1 · F-G8-2 · F-G1-NEW | 1 |
| **B · 镜像 + 启动默认值翻转** | F-IMG-1 · F-PYTHON-1 · F-LIVE-1 (+ F-LAZY-1 default 翻转) | 2 |
| **C · 凭证 staging 重做 + bitable 撤回** | F-G0-1 · F-G1-BLOCK · F-CLI-1 · F-CLI-2 (+ devops Opus 4.7) | 2 |
| **D · 文档/小残缺扫尾** | F-DOC-1/2/3/4 · F-G4-1 · F-G6-1 | 3 |

**核心设计决策**:
1. **onboarding sanitization 撤销** — 不再把 `manager → lead`、`worker_cc/codex/kimi/gemini → agent_a/b/c/d`；蒙眼包直接用代码符号（QA 报告 §5.4 doc-drift 的根因）
2. **manager always-eager + worker_* lazy** — boss 频道必须常驻；worker 成本敏感保留 lazy
3. **群聊 per-agent 前缀路由** — 解析消息前缀 `worker_cc:` / `@worker_cc` / `worker_cc 你好` 三种形式
4. **mock-boss 模式** — bot 自发 + `BOSS_OPEN_ID=$LARK_APP_ID` → 蒙眼无需老板飞书账号
5. **bitable 撤回** — 群消息+员工通讯走 router/inbox 内存+本地 sqlite，不写 bitable（避免 800004135 限流）；只有"老板显式登记任务"才写 bitable
6. **4 flag 默认翻转** + `PYTHONPATH=/app/src` 内置到 Dockerfile

---

## 1 · 命名收回（onboarding sanitization 撤销）

### 1.1 现状

- 主仓 `restructure/team.json` 已是 `manager + worker_cc/worker_codex/worker_kimi/worker_gemini`
- 蒙眼副本 `restructure-onboarding/team.json` 被 sanitize 成 `lead + agent_a/agent_b/agent_c/agent_d`
- QA 真机跑出 F-LEAD-NOLAZY：onboarding 副本里 team.json 的 `lead` ≠ router hard-code 的 `manager` 窗口名 → 主管永不 wake，router 自己另开 window 10 叫 `manager`

### 1.2 修复方案

**撤销 onboarding 的 sanitization 表**（这是 #57 spec §3 的反向操作）。新映射：

| 蒙眼副本旧名 | 新名（=代码符号） | 备注 |
|-------------|------------------|------|
| `lead` | `manager` | 与 router hard-code 对齐 |
| `agent_a` | `worker_cc` | Claude Code 员工 |
| `agent_b` | `worker_codex` | Codex 员工 |
| `agent_c` | `worker_kimi` | Kimi 员工 |
| `agent_d` | `worker_gemini` | Gemini 员工 |
| `tester` (qa_smoke 角色名) | `qa_smoke` | 也撤销，因为内部 skill/log 全用 qa_smoke |

**例外保留**：onboarding 包外壳（README/ONBOARDING.md/SMOKE_GATES.md）继续叫 `蒙眼/blind tester` 这种身份描述，不是 agent 名。

### 1.3 影响文件（onboarding 副本）

| 文件 | 修改 |
|------|------|
| `restructure-onboarding/team.json` | 5 个 agent key 改名 |
| `restructure-onboarding/docs/onboarding/TEAM_LAYOUT.md` | tmux 窗口名表 + 字段说明 |
| `restructure-onboarding/docs/onboarding/SMOKE_GATES.md` | G1/G3/G6/G8 期望文本里 agent 名 |
| `restructure-onboarding/docs/onboarding/ONBOARDING.md` | "lead 是主管" 改成 "manager 是主管" |
| `restructure-onboarding/docs/onboarding/FEISHU_GROUP.md` | 任何引用 agent_a-d / lead 的位置 |
| `restructure-onboarding/scripts/onboarding/say_as_boss.sh` | 注释里的 agent 名（如有） |
| `restructure-onboarding/.env.example` | 任何含 agent 名的变量 |

### 1.4 解决的 finding

- ✅ **F-LEAD-NOLAZY** (P0): manager 名对齐，router 直接路由到 team.json 的 manager
- ✅ **F-G3-1** (P3): /team 卡片现在能查到 manager（在 team.json 里）
- 部分缓解 **F-G8-1** (路由层还需改前缀解析，见 §3)

### 1.5 onboarding sanitization 是不是全部撤销？

**保留**：仓库副本路径仍叫 `restructure-onboarding/`、tmux session 仍叫 `onboarding-blind`、HOME 仍 `/home/admin/onboarding-tester-home`、群聊 chat_id 仍带 `oc_*`、COMPOSE_PROJECT_NAME 仍 `onboarding-blind`。这些是 sandbox 隔离边界，**不是**代码符号，必须保留以防污染主队。

**撤销**：team.json + 文档里的 agent 名。

---

## 2 · 路由层重设计（F-G8-1 + F-LAZY-1 + F-G1-NEW）

### 2.1 manager always-eager / worker_* lazy

**修改点**: `scripts/lib/agent_lifecycle.sh` 的 spawn/lazy 决策。

```bash
# 新增 LAZY_ELIGIBLE 白名单（环境变量）
LAZY_ELIGIBLE="${CLAUDETEAM_LAZY_AGENTS:-worker_cc,worker_codex,worker_kimi,worker_gemini}"

# 在 spawn 决策处
agent_is_lazy_eligible() {
  local agent="$1"
  case ",${LAZY_ELIGIBLE}," in
    *,"${agent}",*) return 0 ;;
    *) return 1 ;;
  esac
}

# manager 走 eager 路径（即使全局 CLAUDETEAM_LAZY_MODE=1 也强制起 UI）
```

**默认值**（生产 + 蒙眼都适用）：
- `CLAUDETEAM_LAZY_MODE=1`（保留全局开关）
- `CLAUDETEAM_LAZY_AGENTS=worker_cc,worker_codex,worker_kimi,worker_gemini`（manager 不在内 → 强制 eager）

蒙眼场景如需"全员 eager"调试，可在 `.env.smoke` 设 `CLAUDETEAM_LAZY_AGENTS=`（空值=禁用 lazy）。

### 2.2 群聊 per-agent 前缀路由

**修改点**: `scripts/feishu_router.py` 的 boss-message 处理函数（接收群聊文本后决定 inject 哪个窗口）。

**前缀解析规则**（按优先级）:

```
1. @worker_cc / @manager  → @-mention 显式定向
2. worker_cc:<text>       → 冒号定向
3. worker_cc <text>       → 起始词定向（仅当起始词命中 agent 名才生效）
4. (兜底)                  → manager 窗口
```

**实现伪码**:

```python
AGENT_NAMES = ("manager", "worker_cc", "worker_codex", "worker_kimi", "worker_gemini")
PREFIX_RE = re.compile(
    r"^\s*(?:@|)(manager|worker_(?:cc|codex|kimi|gemini))(?:\s*[:：]\s*|\s+)",
    re.IGNORECASE,
)

def route_boss_message(text: str) -> tuple[str, str]:
    """Returns (target_agent, stripped_text)."""
    m = PREFIX_RE.match(text)
    if m:
        target = m.group(1).lower()
        body = text[m.end():].strip()
        return target, body
    return "manager", text  # default
```

**关键约束**：
- 大小写不敏感（worker_cc / Worker_CC / WORKER_CC 都行）
- 前缀剥离后 body 才注入（避免 worker pane 看到自己的名字）
- 不识别的前缀（如 `worker_xx`）不当成 agent，整段交给 manager

### 2.3 manager 收消息后的 broadcast 责任（F-G1-NEW）

manager 受到 "全员报道" 这种群消息时应主动 broadcast 给 worker_*。

**修改点**: manager prompt template（`scripts/manager_patrol_prompt_loop.py` 或 inject 初始 prompt）。

加一段:

```
当老板发"全员报道"/"全员汇报"/"@all"等广播性指令时:
  1. 先自己 say 一条 ack
  2. 用 feishu_msg.py inbox-send 给每个 worker_* 各派一条任务
  3. 不要在群里直接 say 4 次（避免刷屏）
```

这是 prompt 层的行为规范，不改代码逻辑。

### 2.4 解决的 finding

- ✅ **F-G8-1** (P0): per-agent 前缀路由
- ✅ **F-LAZY-1** (P0): manager always-eager（QA 看到 "全员 💤" 不会再发生）
- ✅ **F-G1-NEW** (P1): manager 主动 broadcast
- 部分缓解 **F-G8-2** (P3): wake 后 `.agent_sessions.json` 会被 lifecycle 创建（既有逻辑，不改）

---

## 3 · 凭证 staging 重做（F-G0-1 + F-G1-BLOCK）

### 3.1 现状问题

- `.env.example` 是占位符，蒙眼 tester 无飞书账号无法填实值
- `say_as_boss.sh` 强依赖 `lark-cli login --as user` device flow → 蒙眼 CLI 无浏览器、老板飞书账号也不归 tester

### 3.2 双轨方案

**轨道 A · stage `.env.smoke`**（蒙眼场景默认）:

新增 `restructure-onboarding/.env.smoke`，结构:

```
# === Smoke profile (blind onboarding) ===
LARK_APP_ID=cli_a96xxxxxx        # 已脱敏的 stage bot
LARK_APP_SECRET=xxxxxx           # stage bot secret
BOSS_OPEN_ID=ou_b5300000000000   # 老板 open_id（仍指向真老板，便于 router 识别）
CLAUDETEAM_INSTALL_CLAUDE_CODE=1
CLAUDETEAM_ENABLE_FEISHU_REMOTE=1
CLAUDETEAM_LAZY_AGENTS=worker_cc,worker_codex,worker_kimi,worker_gemini
PYTHONPATH=/app/src

# === Mock-boss 模式（蒙眼专用） ===
CLAUDETEAM_BOSS_MOCK=1            # 让 router 把 "bot 自发的消息" 也当 boss-message
CLAUDETEAM_BOSS_MOCK_OPEN_ID=$LARK_APP_ID  # 模拟 boss = bot 本身
```

`.env.example` **保留** 给生产配置参考（不删，加注释说"蒙眼用 .env.smoke"）。

BUILD.md §1 改成:

```
蒙眼 tester:    cp .env.smoke  .env
生产部署:       cp .env.example .env  (然后填真值)
```

**轨道 B · mock-boss 模式**（替代 device flow）:

修改 `scripts/feishu_router.py` 的 boss-detect 逻辑：

```python
def is_boss_message(event) -> bool:
    sender = event.sender.open_id
    if sender == BOSS_OPEN_ID:
        return True
    # mock-boss: 蒙眼场景把 bot 自发的 say 也当 boss
    if BOSS_MOCK and sender == BOT_OPEN_ID:
        return True
    return False
```

`scripts/onboarding/say_as_boss.sh` 重写：

```bash
# 优先 mock-boss: bot 自发，绕开 device flow
if [ "${CLAUDETEAM_BOSS_MOCK:-0}" = "1" ]; then
  exec npx @larksuite/cli im +chat-messages-create \
    --as bot \
    --params "$(jq -n --arg cid "$CHAT_ID" --arg msg "$1" \
      '{receive_id: $cid, msg_type: "text", content: ({text: $msg} | tostring)}')"
fi

# 否则 device-flow user_token (生产场景或 manager 已 stage)
exec npx @larksuite/cli im +chat-messages-create --as user --params ...
```

### 3.3 lark-cli 命令矫正（F-CLI-1）

`scripts/onboarding/create_group.sh` 的 `lark-cli im +get-chat-link --as bot` 在 v1.0.19 不存在。改成：

```bash
npx @larksuite/cli im chats link \
  --as bot \
  --params "$(jq -n --arg cid "$CHAT_ID" '{chat_id: $cid}')"
```

（参数 `--params` 而非位置参数；命令是 `chats link` 不是 `+get-chat-link`。）

### 3.4 bot creds 蒙眼可读（F-CLI-2）

现状 host `/home/admin/runtime/.../creds/lark-cli/` 是 `root:root 600`，蒙眼 admin 用户读不到。

修复：onboarding 包同捆 `restructure-onboarding/.lark-cli-credentials/local-share/` 目录，权限 `admin:admin 644`，绑定到容器 `:ro`。BUILD.md 显式说明此目录已 staged。

### 3.5 解决的 finding

- ✅ **F-G0-1** (P0): `.env.smoke` 提供
- ✅ **F-G1-BLOCK** (P0): mock-boss 替代 device flow
- ✅ **F-CLI-1** (P3): 命令矫正
- ✅ **F-CLI-2** (P2): creds 重定位 + 权限

---

## 4 · 4 flag 默认值翻转

### 4.1 改动表

| Flag | 现默认 | 新默认 | 改动位置 | 备注 |
|------|--------|--------|----------|------|
| `CLAUDETEAM_INSTALL_CLAUDE_CODE` | 0 | **1** | `Dockerfile` build-arg | 蒙眼/生产都需 claude CLI |
| `CLAUDETEAM_ENABLE_FEISHU_REMOTE` | 0 | **1** | `.env.example` + `docker-compose.yml` | 蒙眼/生产都 live mode |
| `CLAUDETEAM_LAZY_MODE` | 1 | **1** (保留)，但 manager 不 lazy | `scripts/lib/agent_lifecycle.sh` | 引入 `LAZY_AGENTS` 白名单 |
| `PYTHONPATH` | unset | **`/app/src`** | `Dockerfile` ENV + entrypoint export | 一行修一切 |

### 4.2 Dockerfile 改动

```dockerfile
# 既有
ARG CLAUDETEAM_INSTALL_CLAUDE_CODE=0   # ← 改 1
# ...

# 新增
ENV PYTHONPATH=/app/src
```

### 4.3 docker-compose.yml 改动

```yaml
environment:
  CLAUDETEAM_ENABLE_FEISHU_REMOTE: "${CLAUDETEAM_ENABLE_FEISHU_REMOTE:-1}"   # ← default 1
  CLAUDETEAM_LAZY_AGENTS: "${CLAUDETEAM_LAZY_AGENTS:-worker_cc,worker_codex,worker_kimi,worker_gemini}"
  PYTHONPATH: "${PYTHONPATH:-/app/src}"
```

prod-hardened compose（如仍保留）显式 opt-out：

```yaml
environment:
  CLAUDETEAM_ENABLE_FEISHU_REMOTE: "0"   # 生产可显式关
```

### 4.4 entrypoint 兜底

`scripts/docker-entrypoint.sh` 顶部加：

```bash
# 兜底：以防 ENV/build-arg 都漏配
export PYTHONPATH="${PYTHONPATH:-/app/src}"
```

### 4.5 解决的 finding

- ✅ **F-IMG-1** (P0): claude CLI 默认装上
- ✅ **F-PYTHON-1** (P0): PYTHONPATH 三道注入（Dockerfile ENV / compose ENV / entrypoint export）
- ✅ **F-LIVE-1** (P0): router/kanban 默认起
- ✅ **F-LAZY-1** (P0): 通过 `LAZY_AGENTS` 白名单实现（manager 不在内）

---

## 5 · bitable 撤回（群消息 + 员工通讯）

### 5.1 现状

`scripts/boss_todo.py` + `scripts/feishu_router.py` 把每条群消息和员工 inbox 也写入 bitable，触发 800004135（每分钟限流）。

### 5.2 撤回边界

| 场景 | 之前 | 之后 |
|------|------|------|
| 群消息 → router 派分 | 写 bitable | 走内存队列 + 本地 sqlite（`/app/scripts/runtime_config.json` 兼容路径），**不写 bitable** |
| 员工 ↔ manager inbox | 写 bitable | 走 `feishu_msg.py` 现有本地 file-based 队列（`.agent_inbox/`），**不写 bitable** |
| 老板**显式登记**任务（"老板加任务: ..."） | 写 bitable | **保留写 bitable**（这是老板 todo 看板的本职） |
| /team /usage 卡片 | 不涉及 | 不涉及 |

### 5.3 实现路径

**修改点 1**: `scripts/feishu_router.py` 把 enqueue → bitable 的调用改成 enqueue → local queue。删除（或注释）所有 `boss_todo.add_*` 在群消息接收路径上的调用。

**修改点 2**: `scripts/boss_todo.py` 留接口，但缩小调用面：
- 仅 `task_register_explicit(text)` 这一条入口写 bitable（trigger：消息文本含 "老板加任务"/"new boss task"/"#todo" 显式标记）
- 其他 `task_log_*` 类 helper 标记为 deprecated，不再被 router 调用

**修改点 3**: 保留 bitable schema 字段不变（向后兼容），只改"写入触发条件"。

### 5.4 验证

蒙眼 tester 跑 G1-G8 全程，bitable 应为空（除非 tester 显式发"老板加任务"）。manager `feishu_msg.py inbox` 仍能正常收派。

### 5.5 与既有 finding 的关系

不在 19 finding 内（老板 4 条新指令之一）。但跟 F-G8-1 路由重设同时改，避免 batch 拆乱。

---

## 6 · devops 模型升级 Opus 4.7

### 6.1 任务

老板要求"构建容器员工（devops）模型升级到 Opus 4.7 确认"。

### 6.2 现状判断

- restructure team.json `manager.cli=claude-code, model=sonnet`
- restructure 没有独立的 `devops` 角色 — devops 职责由 manager 担任（容器构建/运维/补 stage）
- claude-code CLI 默认 sonnet（Sonnet 4.6）

### 6.3 修改方案

**修改点**: `team.json` 给 manager 显式指定 model:

```json
"manager": {
  "role": "主管 + devops",
  "emoji": "🎯",
  "color": "blue",
  "cli": "claude-code",
  "model": "opus-4-7"
}
```

并在 `scripts/cli_adapters/claude_code.py` 的 `spawn_cmd` / `resume_cmd` 加上 `--model` 参数（如未支持需查 claude CLI 文档；如不支持 CLI 层指定，则改 `~/.claude/settings.json` 的 `defaultModel`）。

### 6.4 验证

```bash
docker compose -p onboarding-blind exec team \
  tmux capture-pane -p -t onboarding-blind:manager | grep -i "opus\|sonnet"
# 期望看到 "Opus 4.7" 字样
```

### 6.5 待确认

如果 claude CLI v1.0.x 不支持 `--model` 显式注入，回退到 settings.json 默认值方案；coder 在实施时验证并选最佳路径。

---

## 7 · P1 / P2 / P3 修复细化

### 7.1 P1 (1 项)

| ID | 修复 | 涉及文件 |
|----|------|---------|
| **F-G1-NEW** | manager prompt 加 broadcast 规则（§2.3） | `scripts/manager_patrol_prompt_loop.py` 或初始 inject prompt |

### 7.2 P2 (4 项)

| ID | 修复 | 涉及文件 |
|----|------|---------|
| **F-DOC-2** | pin 文件路径改 `${HOME}/onboarding_chat.id`（不要 `~/.onboarding-tester-home/...`） | `restructure-onboarding/scripts/onboarding/say_as_boss.sh` + `FEISHU_GROUP.md` |
| **F-CLI-2** | 见 §3.4 已覆盖 | `restructure-onboarding/.lark-cli-credentials/` |
| **F-G4-1** | usage_snapshot 容器内补 cli quota 来源（codex-cli-usage / kimi-cli-usage / gemini-cli-usage 装入镜像） | `Dockerfile` + `scripts/usage_snapshot.py` |
| **F-G6-1** | /send 到 lazy worker 先 wake 再 inject | `scripts/slash_commands.py` 的 `slash_send` handler |

### 7.3 F-G6-1 详化

`/send worker_cc hello` 在 worker_cc 是 💤 时，当前直接 `tmux send-keys -t worker_cc 'hello'` → bash 把 hello 当命令。

修复:

```python
def slash_send(args):
    target, text = parse_send_args(args)
    if not is_agent_alive(target):
        wake_agent(target)            # 既有 lifecycle helper
        wait_until_idle(target, timeout=30)
    inject_to_claude(target, text)    # 既有 helper（往 claude 输入框打字）
```

`wait_until_idle` 用 §architect_pane_diff_idle spec 的 `is_agent_idle`。

### 7.4 P3 (6 项 — 文档活)

| ID | 修复 |
|----|------|
| **F-DOC-1** | TEAM_LAYOUT.md `supervisor_ticker` 改 `supervisor`（实际 tmux window 名） |
| **F-DOC-3** | SMOKE_GATES.md G2 期望从 "6 命令" 改 "9 命令"（列出 /help /team /usage /health /tmux /send /compact /stop /clear） |
| **F-DOC-4** | SMOKE_GATES.md G5 期望从 "窗口列表" 改 "默认 capture 主 manager 最近 10 行" |
| **F-G3-1** | /team 卡片代码补显示 manager 行（命名收回后自动解决，无需改代码） |
| **F-G8-2** | `.agent_sessions.json` 创建逻辑：lifecycle 内已有，wake 后自然生成；文档说明 "首次 wake 后才出现" |
| **F-CLI-1** | 见 §3.3 已覆盖 |

---

## 8 · Coder Batch 分发

### 8.1 Batch 1 — 命名 + 路由 + lazy（P0×4 + P1×1 + P3×2）

**Owner**: coder（建议 worker_cc 或 worker_codex 之一）  
**预期工时**: 60-75 min  
**Hot-update**: 全部 docker cp + tmux 局部重启，**不需要 rebuild**

涉及 finding: F-LEAD-NOLAZY · F-G8-1 · F-LAZY-1 · F-G1-NEW · F-G3-1 · F-G8-2

涉及文件:
- `restructure-onboarding/team.json` (撤销 sanitization)
- `restructure-onboarding/docs/onboarding/{TEAM_LAYOUT,SMOKE_GATES,ONBOARDING,FEISHU_GROUP}.md` (改名)
- `restructure/scripts/feishu_router.py` (per-agent 前缀路由 + boss-detect mock-boss)
- `restructure/scripts/lib/agent_lifecycle.sh` (LAZY_AGENTS 白名单)
- `restructure/scripts/manager_patrol_prompt_loop.py` (broadcast prompt)

依赖：无（最先做）。

### 8.2 Batch 2 — 镜像默认值翻转 + 凭证 staging（P0×4 + P2×1 + P3×2 + 老板指令×2）

**Owner**: coder（devops 优先，跟 Batch 1 可并行）  
**预期工时**: 60-90 min  
**Hot-update**: Dockerfile 改动需 rebuild；compose ENV / entrypoint 改动 docker cp；`.env.smoke` 是新文件直接发

涉及 finding: F-IMG-1 · F-PYTHON-1 · F-LIVE-1 · F-G0-1 · F-G1-BLOCK · F-CLI-1 · F-CLI-2 · F-DOC-2

加 老板指令: bitable 撤回（§5）+ devops Opus 4.7（§6）

涉及文件:
- `restructure/Dockerfile` (ARG default 1, ENV PYTHONPATH)
- `restructure/docker-compose.yml` (ENV defaults)
- `restructure/scripts/docker-entrypoint.sh` (export PYTHONPATH 兜底)
- `restructure/team.json` (manager.model = opus-4-7)
- `restructure/scripts/cli_adapters/claude_code.py` (model 注入或 settings.json 路径)
- `restructure/scripts/feishu_router.py` (BOSS_MOCK 检测 + 删 bitable 写入 — 跟 Batch 1 同文件需顺序合并)
- `restructure/scripts/boss_todo.py` (缩窄写入触发)
- `restructure-onboarding/.env.smoke` (新建)
- `restructure-onboarding/.env.example` (注释指向 .env.smoke)
- `restructure-onboarding/scripts/onboarding/{say_as_boss,create_group}.sh` (mock-boss + lark-cli 命令矫正)
- `restructure-onboarding/.lark-cli-credentials/local-share/` (creds stage)
- `restructure-onboarding/docs/onboarding/{BUILD,FEISHU_GROUP}.md` (蒙眼路径说明)

依赖：跟 Batch 1 在 `feishu_router.py` 有冲突 — coder 必须在 Batch 1 合入后再开 Batch 2，或改成同一 PR。建议 **Batch 1 + Batch 2 合并成单 PR**，由同一 coder 串行做。

### 8.3 Batch 3 — 文档/usage 扫尾（P2×2 + P3×4）

**Owner**: coder（任何角色，doc-heavy）  
**预期工时**: 30-45 min  
**Hot-update**: 全部 docker cp，无需重启

涉及 finding: F-DOC-1 · F-DOC-3 · F-DOC-4 · F-G4-1 · F-G6-1 · F-CLI-1（如未在 Batch 2 完成）

涉及文件:
- `restructure-onboarding/docs/onboarding/{TEAM_LAYOUT,SMOKE_GATES}.md`
- `restructure/scripts/slash_commands.py` (/send wake-then-inject)
- `restructure/Dockerfile` (apt install codex-cli-usage / kimi-cli-usage / gemini-cli-usage — 如不存在则先跑通 npm/pypi 安装方式) → 若需要 rebuild，归 Batch 2
- `restructure/scripts/usage_snapshot.py` (容器内 quota 来源)

依赖：Batch 1+2 完成后做。

### 8.4 总览

```
[Batch 1+2]  --(rebuild)-->  [回 host 真机冒烟]  -->  [Batch 3 doc 扫尾]
```

---

## 9 · 实施依赖图

```
F-PYTHON-1  ─┐
F-IMG-1     ─┼─→  rebuild image  ─┐
F-LIVE-1    ─┘                     │
                                   ├─→  容器起来  ─→  G0/G1 通
F-G0-1      ─┐                     │
F-G1-BLOCK  ─┼─→  .env.smoke + mock-boss ─┘
F-CLI-1/2   ─┘

(命名收回)  ─→  team.json + 文档      ─┐
F-LAZY-1    ─→  LAZY_AGENTS 白名单    ─┼─→  manager 常驻 + worker 可 lazy ─→  G2-G7 通
F-LEAD-NOLAZY ─┘                       │
                                       │
F-G8-1      ─→  per-agent 前缀路由   ──┴─→  G8 通

F-G1-NEW    ─→  manager broadcast prompt   ─→  G1 通到位

(P2/P3 扫尾)  ─→  doc 改字 / wake-then-inject / usage CLI ─→  全 doc 一致
```

---

## 10 · QA Smoke 从头入场断言（manager 不介入）

蒙眼 tester 拿 onboarding 包后必须能跑通：

```
G0 [入场 0-5min]:
  ✅ cp .env.smoke .env  (一行)
  ✅ docker compose -p onboarding-blind build  (含 claude CLI, PYTHONPATH 已设)
  ✅ docker compose -p onboarding-blind up -d
  ✅ tmux capture-pane -t onboarding-blind:manager 看到 Claude UI banner（不是 💤）
  ❌ 期望失败时输出: 容器无 manager 窗口 / claude 未起 / PYTHONPATH 缺失

G1 [全员报道 5-10min]:
  ✅ ./scripts/onboarding/create_group.sh  (mock-boss 模式不需 device flow)
  ✅ ./scripts/onboarding/say_as_boss.sh "全员报道"  (mock-boss bot 自发)
  ✅ 90s 内: manager say 1 条 ack；worker_cc/codex/kimi/gemini 各 say 1 条自报名+CLI
  ❌ 期望失败时输出: 群里 say 数量 ≠ 5

G2-G7 [斜杠命令 10-15min]:
  ✅ /help → 9 命令卡片
  ✅ /team → manager + 4 worker 行（不全 🛑，至少 manager 是 ▶）
  ✅ /usage → 4 CLI 全有数据（claude/codex/kimi/gemini）
  ✅ /tmux → manager pane 最近 10 行
  ✅ /send worker_cc hello → worker_cc wake 且 hello 出现在 claude 输入框（不是 bash 报错）
  ✅ /compact → 2 张回执卡

G8 [lazy-wake 15-20min]:
  ✅ 群里发 "worker_cc 你好，请报到一下"
  ✅ 30s 内: worker_cc 从 💤 wake；ps -ef 看到 claude --resume <uuid> 进程
  ✅ worker_cc say 一条回应
  ❌ 期望失败时输出: router 把消息路由到 manager 而不是 worker_cc
```

**总时长预算**: 20 min（manager 不介入）。如超 30 min 视为 spec 失败。

---

## 11 · 回滚 / 风险

| 改动 | 风险 | 回滚 |
|------|------|------|
| 命名收回（team.json + docs） | 蒙眼 tester 看到代码符号可能困惑 | onboarding/ONBOARDING.md 加一句 "agent 名是代码符号，不是身份伪装" |
| BOSS_MOCK 模式 | 生产环境如误开会把 bot 自言自语当老板指令 | mock-boss 守卫: 仅当 `CLAUDETEAM_BOSS_MOCK=1` 且 `CLAUDETEAM_RUNTIME_PROFILE=smoke` 双条件成立时启用；prod 默认双 0 |
| 4 flag 默认翻转 | 生产部署如未审视，可能误装 claude CLI（增构建时间） | prod-hardened compose 显式 `CLAUDETEAM_INSTALL_CLAUDE_CODE=0` opt-out，并加 CHANGELOG 警示 |
| bitable 撤回 | boss_todo 历史数据保留，但新任务不写 → 老板看板可能感觉"不更新" | 仅"群消息+员工 inbox"撤回，老板显式 "/todo add" 仍写 bitable |
| LAZY_AGENTS 白名单 | manager 强制 eager 增成本（启动 sonnet）+ 占进程槽 | 由 Opus 4.7 接 manager 后，单一进程；worker 仍 lazy，整体成本可控 |
| Opus 4.7 模型 | 如 claude CLI 不支持 `--model` 注入，需走 settings.json 全局默认 → 影响其他 worker_cc | 退路：在 spawn 前临时 cp 一份 settings.json 到 manager 专属 HOME；coder 验证后选 |

---

## 12 · 验收 checklist（给 review/qa）

- [ ] team.json 全部用 manager + worker_cc/codex/kimi/gemini（无 lead/agent_a-d）
- [ ] tmux session 起来后 manager pane 看到 claude UI（非 💤）
- [ ] worker_* pane 仍 💤 待 wake（lazy 保留）
- [ ] router 解析群聊 "worker_cc 你好" → 路由到 worker_cc 窗口（不再全打 manager）
- [ ] router 解析无前缀消息 → 默认路由 manager（兜底）
- [ ] mock-boss 模式: bot 自发的 say 被 router 当 boss-message 处理
- [ ] `.env.smoke` 文件存在，cp 后容器一次启动成功
- [ ] `docker exec team which claude` 返回路径
- [ ] `docker exec team echo $PYTHONPATH` 返回 `/app/src`
- [ ] `docker exec team python3 -c "import claudeteam"` 不报错
- [ ] /usage 卡片显示 claude/codex/kimi/gemini 4 项数据
- [ ] /send worker_cc hello → worker_cc 先 wake 再收 hello
- [ ] G1 全员报道触发 5 条 say（manager + 4 worker）
- [ ] G8 群聊 prefix-route 触发 worker_cc wake，进程 cmdline 含 `--resume <uuid>`
- [ ] bitable 在 G1-G8 全程不被写入（除非显式 "老板加任务"）
- [ ] manager pane 跑的是 Opus 4.7（capture-pane 看 model 字样）
- [ ] qa_smoke 从零跑全程 ≤ 30 min 不需 manager 介入

---

## 13 · 给 manager 的派工建议

- **同 coder 串行做 Batch 1+2**（feishu_router.py 共享文件，避免 merge 冲突）
- **Batch 3 可派给 doc-friendly 角色**（worker_kimi 适合，文档手感好）
- **rebuild 时机**: Batch 2 完成后一次性 rebuild；Batch 1 + Batch 3 全 docker cp
- **review 抓重点**: §2.2 路由前缀正则的边界（大小写/分隔符）+ §3.2 mock-boss 守卫双条件 + §6 Opus 4.7 注入路径
- **qa_smoke 必须从空容器开始**（`docker compose down -v`），不能基于已有 state

---

**End of spec**
