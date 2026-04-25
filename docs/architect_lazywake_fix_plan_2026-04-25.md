# Lazy-Wake 修复方案（架构设计 v1）· 2026-04-25

**作者**：architect
**输入依据**：
- `docs/lazy_wake_resume_smoke_2026-04-25.md`（qa_smoke 本轮冒烟全记录）
- `docs/live_container_smoke.md §Lazy-Wake Resume Smoke`（runbook）
- `scripts/lib/agent_lifecycle.sh`（suspend/wake 实现）
- `scripts/supervisor_tick.sh` + `scripts/docker-entrypoint.sh:561-570`（ticker 循环）
- `src/claudeteam/cli_adapters/*.py`（仅 `claude_code.py` 的 `resume_cmd` 非 None）
- `DEPLOYMENT_ISSUES.md §R7`（supervisor ticker 装配史）

**范围边界**：
- 只出设计；代码留给 coder
- 不重构 lazy-wake 骨架（suspend→sid→resume 已验证通畅，只在表层补东西）
- 方案按 P 切片，每一片独立验收；建议 **本轮做 P0+P1，P2/P3 排进下一轮**
- 热更新友好：P0+P1+P3 全部可走 `docker cp` + 局部窗口重启；P2 里 codex 如改 `run_codex_cli.sh` 同样热更；不出现"必须 rebuild"

---

## 0. TL;DR

qa_smoke 本轮证明：**lazy-wake 机制链路通畅**（suspend→save sid→kill→wake→resume cmdline 完全对齐）。语义层"答出锚点"卡在 worker_cc OAuth 401，**不是 lazy-wake bug**，是凭证管理漏洞。

本方案的核心思想：**不动 lazy-wake 骨架，修两件事让闭环跑通 → 一件事让覆盖面从 1/5 扩到 5/5 → 一件小事防踩坑**。

| 优先级 | 问题                                       | 本轮范围？ | 改动影响面                   | 是否要 rebuild |
|--------|--------------------------------------------|------------|------------------------------|----------------|
| P0     | worker_cc OAuth token 过期无感             | ✅ 本轮    | 新增 1 脚本 + entrypoint 1 行 + runbook 前置检查 | 否（docker cp） |
| P1     | supervisor 自动 SUSPEND 未触发             | ✅ 本轮    | 补 supervisor 工作区 + 两行 tick.sh 保护逻辑 + entrypoint 起始健康拉起 | 否（docker cp） |
| P2     | codex/gemini/kimi/qwen 的 resume_cmd=None | 🟡 下一轮  | 4 个 adapter + 1 个 helper；可按 CLI 分批 | 否 |
| P3     | tmux send-keys 长命令换行断句              | 🟡 下一轮  | 新增 1 helper，runbook 明示  | 否 |

---

## 1. P0 · worker_cc OAuth token 过期（本轮必修）

### 1.1 根因

`/home/claudeteam/.claude/.credentials.json` 里存放 Claude OAuth device-flow 换得的短期 `access_token`（小时级）和长期 `refresh_token`（周级）。当前容器内：

1. `access_token` 约 6 小时后到期；
2. Claude Code CLI 首次启动时会把 `access_token` 读进内存后续复用，内存副本撑到进程生命周期结束 → 这解释了为什么 **manager 不报 401 但后启动的 worker_cc 直接 401**（manager 是容器启动那秒开的进程，token 还新鲜；worker_cc 是后来 spawn 的新进程，读到的是已过期的 token）；
3. Claude Code CLI 非交互下**不会**自动 refresh；只有交互执行 `/login` 或首次 device flow 时才写回 `.credentials.json`；
4. 容器里挂载的 `.claude-credentials/` 是从主机投影进来的；主机侧也没人定时 refresh；
5. supervisor_tick 每次 wake worker 的时候读的还是磁盘上那份过期 token → wake 也救不回来。

**结论**：过期是必然会发生的（几小时一轮），而现有架构里**没有任何一个组件在到期前做续期或到期时报警**。lazy-wake 只是把这个慢性病显化了。

### 1.2 方案

采用**分层兜底**：A 优先（自动续期），B 保底（过期提醒），C 冷兜底（切 API key）。**本轮先落 A + B；C 写进 runbook 作为灾备，不入代码。**

#### 方案 A · 容器内 token 健康守护进程（推荐，MVP 一个脚本）

- 新增 `scripts/claude_token_guard.sh`（容器内运行）：
  - 每 30 分钟跑一次：
    1. 读 `.credentials.json`，解析 token 过期时间（oauth token JWT 内有 exp，也可从文件 mtime + 粗略 TTL 推断）；
    2. 如果**距离过期 < 60 分钟**：发 feishu 通知到 manager inbox，文案 `⚠️ Claude OAuth token 将在 X 分钟内过期，请 host 侧执行 "claude /login" 刷新`；
    3. 如果**已过期**：再发一条高优，标 `🚨 Claude OAuth token 已过期，所有新 claude 进程将 401`；
    4. 同时写 `/app/state/claude_token_status.json`（字段：`expires_at`, `minutes_left`, `last_check`, `status=ok|warning|expired`），供 watchdog / /usage / 未来 slash 命令读。
- entrypoint 追一行在 watchdog 之后启动这个守护（后台 `setsid ... &` 即可，不需要独立 tmux 窗口）。
- 决策不做"容器内自动刷新 token"：device flow 需要浏览器，容器里根本点不了；硬写 refresh 端点会和 anthropic.com 的 OAuth scope 校验掐架，风险高收益低。**容器只负责发现 + 通知，刷新由人干。**

#### 方案 B · runbook 前置硬断言 + 自动阻断

- `scripts/preflight_claude_auth.sh`（host 侧或容器内跑都可以）：
  - 读 credentials，推断 TTL；
  - `<60min` → 退 exit 2 + 打印"请先 host 侧刷新再继续"；
  - 已过期 → 退 exit 3 + 打印相同提示；
  - `>=60min` → exit 0。
- docker-entrypoint 在拉 tmux 窗口之前 run 一次这个 preflight；**fail 则不拉工作窗口，只留 manager 窗口并在 pane 上挂大红横幅**（不要 exit 容器，容器退出比"挂着让老板看见"更糟）。
- runbook v2.2 的 "Preflight" 章节把这一步写进去。

#### 方案 C · 切 ANTHROPIC_API_KEY（灾备，不入本轮代码）

- OAuth 是零成本（Claude Max 订阅），切 API key 要额度消耗；所以只当"人实在没空登录"的最后一招。
- runbook 里写一节"紧急切 API key 模式"：在 `.env` 填 `ANTHROPIC_API_KEY=sk-ant-...` 后重建容器；Claude Code CLI 会优先读 env，不碰 `.credentials.json`。
- **本轮不落代码；只出文档**。

### 1.3 给 coder 的清单（P0）

| # | 文件                                   | 改成什么                                                                                        | 验收                                                                                                            |
|---|----------------------------------------|-------------------------------------------------------------------------------------------------|-----------------------------------------------------------------------------------------------------------------|
| 1 | `scripts/claude_token_guard.sh`（新建） | while-sleep 30min 循环；读 `.credentials.json` → 判 TTL → 发 feishu + 写 state json             | 容器内手动跑 `bash scripts/claude_token_guard.sh --once` 三种场景（健康/告警/过期）均能：打印结果 + 写 state json + 告警场景发 inbox |
| 2 | `scripts/preflight_claude_auth.sh`（新建） | 单次执行；输出简短 ok/warning/expired + exit 0/2/3                                              | 手动改 `.credentials.json` mtime 伪造过期，脚本退 3；恢复后退 0                                                 |
| 3 | `scripts/docker-entrypoint.sh`         | ① 在拉 tmux 主循环前调 preflight，fail 则拉只含 manager 的窗口并挂红横幅；② watchdog 之后 nohup 起 guard | 重建容器两种情况都能走通；`docker logs` 能看到 preflight 和 guard 各自的启动行                                   |
| 4 | `state/` 目录约定                       | guard 写 `/app/state/claude_token_status.json`                                                  | 字段齐：expires_at / minutes_left / last_check / status                                                         |
| 5 | `docs/TROUBLESHOOTING.md`              | 新增"Claude OAuth 401"节：症状 → preflight 怎么跑 → host 侧 `/login` 步骤                       | 内容完整即可                                                                                                     |

**不在 coder 代码范围、需外部执行**：
- 每次 401 的实际刷新，需 host 侧 `claude /login` 走浏览器 device flow。这一步是 **manager / 运维** 的动作，不是代码能解决的。文档里必须明示。

### 1.4 trade-off

- 方案 A 的 TTL 推断不绝对精确（access_token 的 exp 可能不在文件里，按 mtime + 保守 5 小时估算即可；告警宁可早不要晚）。
- guard 和 watchdog 放在一起 vs 独立脚本：独立脚本更好，watchdog 职责是"进程崩没崩"，guard 是"凭证到没到期"，混在一起将来难拆。
- 如果未来老板愿意切 API key 模式，方案 A/B 都仍然兼容（guard 检测到 `ANTHROPIC_API_KEY` 非空就早退 exit 0 + 输出"API key mode, skipping oauth guard"）。

---

## 2. P1 · supervisor 自动 SUSPEND 未触发（本轮必修）

### 2.1 根因

本轮 qa_smoke 看到几个现象共同指向同一个病：

1. `agents/supervisor/workspace/` 目录**不存在**（`ls` 确认）；
2. 因此 tick 时的 prompt 里 `overrides.json` / `decisions/` 都指向不存在的路径；
3. tick 日志反复出 `🌅 wake_agent: supervisor 冷启动 — 无 saved session`，说明 supervisor 从来没跑出过有效 session_id；
4. supervisor 每次被 wake 都是冷启动，需要重新读 prompt、连 API、思考、产出决策；60s interval 根本不够它跑完一轮；
5. supervisor 自己就是 CC agent，worker_cc 会 401 它也会 401（lazy_wake 冒烟期间 token 已过期）→ 它 wake 后立刻 401 死掉；
6. 结果：supervisor 看起来"在跑"，实际上**每次 tick 都是冷启动 → 401 → 没产出**，自动 SUSPEND 决策从来没落盘过。

这件事**不是 supervisor_tick.sh 的 bug，是 supervisor 这个 agent 从来没真正完整跑过一轮**。

### 2.2 方案

分三段：**先让 supervisor 能跑完一轮 → 再让 tick 有节奏地推它 → 再让它的决策能落地**。

#### 2.2.1 让 supervisor 能跑完一轮（核心）

- 先解决 P0（token），supervisor 的 401 自然消失（同一个凭证）；
- 建 `agents/supervisor/workspace/`：
  - `overrides.json`（允许 manager 指定"永不 SUSPEND"白名单，例如 router / kanban / watchdog-* / supervisor_ticker / manager 本身）；
  - `decisions/`（空目录，tick 往里写 `YYYY-MM-DD.jsonl`）；
  - `README.md`（两句话说明）；
- 初始化 `overrides.json` 默认内容：
  ```json
  {
    "never_suspend": ["manager", "router", "kanban", "watchdog", "supervisor_ticker", "supervisor"],
    "idle_min_override": {}
  }
  ```
- supervisor 的 tick prompt 要求精简成**幂等小粒度**（下一段讲）。

#### 2.2.2 让 tick 有节奏地推它

`scripts/supervisor_tick.sh` 当前逻辑本身是对的（三路径：inject / wake+inject / spawn+inject），但 **prompt 太重**（一句 NL 要 supervisor 读 overrides + 扫所有 agent + 按 NL 规则决策 + 落盘 + 自休眠），冷启动根本来不及。

改动建议（coder 只改 `supervisor_tick.sh`）：
1. prompt 拆成"扫一个 agent + 写一行决策"；每次 tick 只处理一个 agent，轮询；
2. 把"当前要处理的 agent 名"写进 `/app/state/supervisor_cursor.txt`，每次 tick 读 cursor → 处理下一个 → 写回；
3. tick prompt 新模板（伪文）：
   > `读 /app/state/supervisor_cursor.txt 拿到本轮目标 agent=<X>；读 overrides.json 白名单；读 <X> 的 inbox/pane/status；按"idle 超 IDLE_MIN 且 inbox 空"规则输出 SUSPEND/KEEP 一行 JSON 追加到 decisions/今天.jsonl；把 cursor 推进到下一个 agent；回复一句"done <X>"。`
4. 如果决策是 SUSPEND，**不要让 supervisor 自己调 suspend_agent**（权责边界）：只写决策，由 `scripts/supervisor_apply.sh` 下一跳读 decisions jsonl 真去执行 suspend。理由：决策和执行解耦，以后可以人工 review / 延迟执行 / 多层审核。

#### 2.2.3 让决策能落地

- 新建 `scripts/supervisor_apply.sh`：
  - 读当天 decisions jsonl → 找 SUSPEND 且未 apply 的 → 调 `suspend_agent <name>` → 在同一行加 `applied_at`；
  - 由 supervisor_ticker 窗口的 while-sleep 每次 tick 之后也跑一次 apply（一对一次数关系）。

#### 2.2.4 entrypoint 健康拉起

`docker-entrypoint.sh` 启动 supervisor_ticker 窗口前**先**跑一次 `supervisor_tick.sh`（同步），让 supervisor 有机会第一轮就完成一次冷启动。不依赖 15min / 60s 的漫长等待。

### 2.3 给 coder 的清单（P1）

| # | 文件                                             | 改成什么                                                                                   | 验收                                                                                             |
|---|--------------------------------------------------|--------------------------------------------------------------------------------------------|--------------------------------------------------------------------------------------------------|
| 1 | `agents/supervisor/workspace/` 及子结构          | 新建目录；落默认 `overrides.json` + 空 `decisions/` + 一句话 README                        | 目录存在；`overrides.json` 能被 `jq .never_suspend` 解析                                         |
| 2 | `scripts/supervisor_tick.sh`                     | prompt 精简为"处理一个 agent"；加 cursor 文件读写；禁止 supervisor 自己调 `suspend_agent`  | idle_min=3 interval=60 场景下，5 轮 tick 后 `decisions/今天.jsonl` 行数 == 5                     |
| 3 | `scripts/supervisor_apply.sh`（新建）            | 读 jsonl 未 apply 的 SUSPEND → 执行 `suspend_agent` → 标 applied_at                         | 单独 `bash supervisor_apply.sh` 跑一次，抽 1 个 worker 故意做成 idle → 下一轮能看到它被 suspend  |
| 4 | `scripts/docker-entrypoint.sh:566-570`           | ① 拉 ticker 窗口之前同步跑一次 `supervisor_tick.sh`；② 每轮 tick 后追跑 `supervisor_apply.sh` | 新容器起来 3 分钟内 `.agent_sessions.json` 能看到至少 1 条 entry；第 5 分钟能看到 decisions jsonl |
| 5 | `scripts/lib/agent_lifecycle.sh`                 | 无需改（suspend/wake 没问题）                                                               | —                                                                                                |

### 2.4 trade-off

- 拆"决策/执行"会多一个 apply 脚本，但换来的是"可 dry-run / 可人工 review"的长期收益，非常值；
- cursor 文件 vs  一次扫全部：cursor 能把单次 tick 保持在 60s 内完成，适合短 interval；如果未来老板愿意接受 15min interval 再考虑回到一次扫全部；
- overrides.json 白名单在项目里落硬编码 vs 配置文件：配置文件赢，理由是团队扩员后老板能直接改而不用改代码；
- 如果 supervisor 还是偶尔跑飞（prompt 没完成就被下一轮冲掉），可以在 tick.sh 里加 `lock 文件`（上一轮没释放就 skip 本轮）。本轮先不加，留给下一轮观察。

---

## 3. P2 · 4 个非 CC CLI 的 resume_cmd=None（下一轮）

### 3.1 现状

| adapter         | 当前 resume_cmd | CLI 层真机制？                                   | 本轮判断                         |
|-----------------|-----------------|--------------------------------------------------|----------------------------------|
| claude_code.py  | ✅ 有            | `--resume <sid>`（本轮已验证）                   | 保持                             |
| codex_cli.py    | ❌ None（注释"待查"） | codex 0.124+ 有 `codex resume --last` / `codex resume <session_id>`（需实测） | **排下一轮，有的改**             |
| gemini_cli.py   | ❌ None（注释"checkpointing 待核"） | checkpointing 是 `/chat save <tag>` + `/chat resume <tag>` 内部交互，**不是 CLI 启动 flag** | **排下一轮，标"冷启动 by design"+ 注释写清楚** |
| kimi_code.py    | ❌ None          | Moonshot preview，无公开 resume                  | **标"冷启动 by design"**         |
| qwen_code.py    | ❌ None          | 无公开 --resume                                  | **标"冷启动 by design"**         |

### 3.2 方案（下一轮，不展开细节）

- **codex_cli.py**：实测 `codex resume` 子命令；如能拉起上次 session，在 `spawn_cmd` 走的 `run_codex_cli.sh` 里加 `--resume-last` 分支；`resume_cmd` 返回这个分支命令。如果子命令不稳定（常见问题：session 超过某 TTL 失效），采用"best effort resume + fallback to fresh"模式：wake 先试 resume，失败（adapter 可返回一个"fallback token"或抛特定 exit）就 fallback 到 spawn_cmd；
- **gemini_cli.py**：保留 None，但把 adapter docstring 里的"TODO 待核"改成明确的 `resume_cmd: by design None (Gemini 的 checkpointing 是 slash 命令，不是启动 flag；冷启动)`，并在 `wake_agent` 的 log 里加一句"gemini-cli cold-start by design"；
- **kimi_code.py / qwen_code.py**：同上，注释明确 by design；
- **`agent_lifecycle.sh`**：在 wake 走 fallback 冷启动分支时，输出改为 `🌅 wake_agent: <agent> 冷启动 (CLI=<cli_type> resume不支持/by-design)`，明确是"设计如此"不是 "bug 漏实现"。

### 3.3 验收思路

- 把 4 个 worker 各 suspend → wake 一轮，观察 cmdline：codex 如果有 resume 应带 `resume` 子命令；其余三家必须能冷启动成功（不卡 banner、不僵死）；
- 各 adapter 的 docstring 要让新人不用跑代码就知道"有没有 resume、为什么没有"。

**本方案：P2 不入本轮 PR；coder 当前可以忽略。**下轮派工时 manager 再细化每个 CLI 的任务书。

---

## 4. P3 · tmux send-keys 长命令换行断句（下一轮）

### 4.1 现状

tmux pane 会在宽度（默认 80 或 tmux 配置的 `window-size`）处自动换行。bash 从 pane 读到"换行"会按命令结束解析，长 `while ... do ... done` 在某些列数下被切成若干段，报 `syntax error near unexpected token do`。本轮 qa_smoke 踩了一次，临时绕法是写到 `/tmp/*.sh` 再 `bash`。

### 4.2 方案（下一轮）

提供一个 helper，任何调用方想给 pane 塞长命令都走它：

- 新建 `scripts/lib/safe_tmux_send.sh`：
  - 函数 `safe_send_long <target> <payload>`；payload 长度 > 阈值（比如 200 字符）或包含 `do`/`done`/`then` 等结构词 → 写 `/tmp/safe_send_$$_$RANDOM.sh` + `chmod +x` + `tmux send-keys -t <target> "bash /tmp/xxx.sh" Enter`；否则直接 send；
  - 结束后由 helper 自己 `rm -f /tmp/safe_send_$$_*.sh`（或加 trap）；
- 所有现有会发长命令的脚本改调 helper：`supervisor_tick.sh`（已经用 heredoc 规避，但 inject 指令可以走 helper）、运维脚本、runbook 里的 copy-paste 片段；
- `docs/live_container_smoke.md` / runbook 里加一节："给 tmux 塞命令的铁律"。

### 4.3 验收思路

- 故意用 40 列宽 tmux 跑一段 200 字的 bash，直接 send-keys 预期失败；改调 `safe_send_long` 预期成功。

**本方案：P3 不入本轮 PR。**

---

## 5. 本轮 PR 的 coder 交付清单（只看这一节也能动手）

### 本轮落盘（P0 + P1）

**新增文件**：
1. `scripts/claude_token_guard.sh` — OAuth TTL 守护，每 30 分钟跑一次
2. `scripts/preflight_claude_auth.sh` — entrypoint 调用的单次检查
3. `scripts/supervisor_apply.sh` — 读 decisions jsonl 执行 SUSPEND
4. `agents/supervisor/workspace/overrides.json` — 白名单默认值
5. `agents/supervisor/workspace/decisions/.gitkeep`
6. `agents/supervisor/workspace/README.md` — 一句话目录说明

**修改文件**：
7. `scripts/docker-entrypoint.sh` — ① 拉工作窗口前调 preflight（fail 则挂红横幅、不拉 worker）；② watchdog 后 nohup 起 token guard；③ 拉 ticker 窗口前同步跑一次 `supervisor_tick.sh`；④ ticker 循环每轮 tick 后追跑 `supervisor_apply.sh`
8. `scripts/supervisor_tick.sh` — prompt 精简为"处理一个 agent"；加 cursor 文件读写；明示 supervisor 不再自己调 suspend_agent
9. `docs/TROUBLESHOOTING.md` — 新增 "Claude OAuth 401" 节 + "supervisor 没产出" 节

**不改动**：
- `scripts/lib/agent_lifecycle.sh`（已验证可用，本轮不动）
- `src/claudeteam/cli_adapters/*`（P2 范围，本轮不动）

### 全部走 `docker cp` + 局部窗口重启，不 rebuild

- `claude_token_guard.sh` / `supervisor_apply.sh` / `preflight_claude_auth.sh` / `supervisor_tick.sh`：`docker cp` 进去 + 现有 ticker 窗口 Ctrl-C 重起 + guard 新开后台；
- entrypoint 改动对**已运行容器**无效，新容器重建时才生效；本轮容器可以先手动执行一次 preflight + 手动 nohup 起 guard，**不需要 rebuild**；
- `overrides.json` / `README.md` 走 `docker cp`；
- 下次 rebuild 的时候才把 entrypoint 改动"正式固化"。

---

## 6. qa_smoke 验收要点（Plan A 全量 E2E）

### 6.1 机制层（已 PASS，保持）

沿用 `docs/lazy_wake_resume_smoke_2026-04-25.md §4` 的 A/B/C/D 四证据。

### 6.2 P0 新增断言

| 断言 ID | 内容                                                                                               | 如何跑                                                                                      |
|---------|----------------------------------------------------------------------------------------------------|---------------------------------------------------------------------------------------------|
| P0-1    | 健康状态下 preflight exit 0；人为改 mtime 或删 token → exit 非 0                                   | 手动 `bash scripts/preflight_claude_auth.sh; echo $?`                                       |
| P0-2    | Guard 跑一轮后 `/app/state/claude_token_status.json` 存在且字段齐                                  | `cat /app/state/claude_token_status.json ｜ jq .status`                                     |
| P0-3    | 伪造"距离过期 30 分钟" → manager inbox 出现告警消息                                                | 改 mtime 后跑 `bash claude_token_guard.sh --once` → `feishu_msg.py inbox manager`            |
| P0-4    | 伪造"已过期" → manager inbox 出现高优告警                                                          | 同上                                                                                        |
| P0-5    | entrypoint 启动时 preflight 失败 → 只拉 manager 窗口 + pane 挂红横幅；preflight 成功 → 全窗口齐    | `docker compose up -d` 两种场景                                                             |

### 6.3 P1 新增断言

| 断言 ID | 内容                                                                                               | 如何跑                                                                                    |
|---------|----------------------------------------------------------------------------------------------------|-------------------------------------------------------------------------------------------|
| P1-1    | `agents/supervisor/workspace/overrides.json` 存在且 `never_suspend` 数组非空                        | `jq '.never_suspend | length' <file>` > 0                                                 |
| P1-2    | supervisor cold-start 成功且 sessions file 有 supervisor entry（token 刷新后）                      | `cat /app/scripts/.agent_sessions.json ｜ jq .supervisor`                                 |
| P1-3    | idle_min=3 interval=60 场景下，10 分钟内 decisions jsonl 有 ≥ 3 行 SUSPEND/KEEP 决策                 | `wc -l decisions/$(date +%F).jsonl`                                                       |
| P1-4    | 任选一个非白名单 worker 做成 idle 超 3 分钟 → 下一轮 apply 后它进入休眠（pane 💤 banner）           | 手动观察 pane + `cat .agent_sessions.json`                                                |
| P1-5    | 白名单 agent（manager/router/kanban 等）**不会**被 SUSPEND                                          | 决策 jsonl 过滤 `grep manager\|router` → 期望空或只 KEEP                                   |
| P1-6    | `supervisor_apply.sh` 幂等：重复跑两次不会重复 suspend（applied_at 存在则 skip）                    | 连跑两次后看 pane 和状态                                                                  |

### 6.4 端到端语义层（Plan A）

qa_smoke 本轮没能闭环的"答出锚点"，P0 修了之后应能闭环：

- 步骤同 `live_container_smoke.md §Lazy-Wake Resume Smoke §Full-scope (Plan A)`；
- 关键新断言：token guard 启动后 **6 小时内**不再出 401；如出则 preflight + guard 至少其一提前发过告警（inbox 留痕）。

### 6.5 不做的断言（scope exclusion）

- P2 四个 CLI 的 resume 行为 → 下一轮
- P3 safe_send_long helper → 下一轮
- Gate A 全员报道（boss user_access_token 依赖）→ 待 host 侧配完 lark-cli --as user 再覆盖

---

## 7. 风险与对策

| 风险                                                               | 概率 | 对策                                                                                  |
|--------------------------------------------------------------------|------|---------------------------------------------------------------------------------------|
| OAuth refresh_token 本身也过期（周级），guard 检测不到              | 低   | Guard 检测连续 3 次"过期但 host 没响应"→ 上报"可能 refresh_token 也死了"              |
| supervisor cursor 文件损坏                                          | 低   | tick.sh 读 cursor 失败 → 重置到 team.json 第一个 non-whitelist agent                  |
| supervisor_apply.sh 和 ticker 并发（死锁/double-suspend）           | 中   | apply 脚本开头 `flock /tmp/supervisor_apply.lock -w 10` 独占；超时 skip 本轮          |
| preflight 太严苛导致容器频繁"挂红横幅"                              | 中   | 红横幅 + 只留 manager + 5 分钟 grace period（期间 manager 可以人工修）                |
| P0 改完但老板不愿意每周 `/login` → 长期仍会 401                     | 中   | 切 API key 的灾备文档里写明成本 + 开关，老板自己决策                                  |

---

## 8. 排期建议（manager 参考）

- **本轮（阶段 A，1-2 天）**：P0 + P1 全量；qa_smoke 跑 6.2 + 6.3 + 6.4 断言。
- **下一轮（阶段 B，0.5-1 天）**：P3 helper（快，可先做）；P2 codex resume 实测。
- **再下一轮（阶段 C，按需）**：P2 gemini/kimi/qwen 的 docstring 明示 + by-design log；整合所有产出写 runbook v2.2 + 冒烟 skill。

---

## 9. 附录 A · 外部操作清单（不在 coder 代码范围）

以下步骤**代码解决不了**，需 manager / 运维在 host 侧人工执行：

1. **每次 401 / guard 告警后**：host 浏览器跑 `claude /login`，device flow 成功后文件自动刷新；如果容器是 bind-mount host 侧 credentials，容器内立刻生效；如果是独立投影，需要 `docker cp` 覆盖 `/home/claudeteam/.claude/.credentials.json`。
2. **lark-cli user_access_token**：如果要覆盖 Gate A（全员报道 boss 身份），host 侧 `npx @larksuite/cli login --as user`（device flow）后用 bind-mount 把 `~/.lark-cli/config.json` 带进容器。
3. **紧急切 API key 模式**：`.env` 填 `ANTHROPIC_API_KEY=sk-ant-...` → 重建容器。P0 guard 会检测到 env 已设置后自动 exit 0（写 state 标 `api_key_mode`）不再告警。

---

## 10. 附录 B · 一眼看懂的 ASCII 时序

```
          token 发放              ~6h 后 token 过期
             │                        │
 host ──────┴────────────────────────┴──────► 时间
              │                        │
              │  guard 每 30min 扫    │  preflight 拦住新容器起 worker
              │  快到期则 inbox 告警  │  老容器 worker 新进程 401
              │                        │
              └──> manager inbox   ←───┘
                      │
                      ▼
           ⚠️ 提醒人去 host 侧 /login
                      │
                      ▼
             refresh 生效 → guard 下轮回到 ok
```

```
supervisor_ticker 窗口
 │
 ▼ while sleep 60:
     tick.sh ── (spawn|wake|inject) ──► supervisor
                                          │
                                          ▼
                                  读 cursor / overrides
                                  扫当前 agent 一条
                                  写一行 decision.jsonl
                                  cursor 推进到下一个
                                          │
     supervisor_apply.sh  ◄───────────────┘
       │
       ▼ 扫 jsonl 未 apply 的 SUSPEND → suspend_agent → 标 applied_at
```

---

*architect · 本轮产出 · 2026-04-25*
*下一跳：coder 按 §5 清单动手；qa_smoke 按 §6 断言验收。*
