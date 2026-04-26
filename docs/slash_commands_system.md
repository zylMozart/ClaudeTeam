# ClaudeTeam 斜杠命令系统 — 交付文档

- **基线**：`HEAD = 28fef3d`（422182b + 28fef3d 干净状态，picker/form 卡尝试已回退）
- **适用**：维护团队按本文档可在另一套 Claude Code + tmux + 飞书 App 栈上复刻同等能力
- **作者**：toolsmith（ClaudeTeam）· 2026-04-20 北京时间

---

## 1. 背景：为什么要零 LLM 斜杠命令

老板在飞书群指挥 ClaudeTeam（一堆 tmux 里跑的 Claude Code agent），日常高频操作如「看一眼 devops 的窗口」「给某员工压下上下文」「查额度」。这些操作如果全走 manager LLM：

- **烧 token**：每次「看 devops 窗口」都要模型自己去 tmux capture-pane，上下文几百 token，日百次累加是纯浪费
- **延迟高**：模型思考 + 工具调用 2–5 秒
- **不确定性**：同一指令两次响应可能不一样，排障困难
- **不可组合**：终端用户没法用命令行直觉（tab 补全、引号、管道）

所以把这一层拦在 LLM 之前：老板输 `/tmux devops` 就直接 tmux capture-pane，0 token、<300ms、输出稳定、参数显式。manager 完全没介入。

---

## 2. 双入口架构

两个入口都共用 `scripts/slash_commands.py` 的 `dispatch(text)`，但**注入方式不同**：

```
┌─────────────────────────────────────────────────────────────────┐
│                       入口 A：飞书群聊                           │
│  老板在群里: "/team"                                            │
│    ↓  Feishu WebSocket → lark-cli event +subscribe              │
│    ↓  stdin NDJSON                                              │
│  scripts/feishu_router.py handle_event(event)                   │
│    ↓  slash_commands.dispatch(text) → (matched, reply)          │
│    ↓  matched=True: _lark_im_send(chat_id, card=...)            │
│    ↓  matched=False: 正常路由到 manager                          │
└─────────────────────────────────────────────────────────────────┘

┌─────────────────────────────────────────────────────────────────┐
│                     入口 B：manager 本机                         │
│  manager 在 Claude Code prompt 输: "/tmux devops"               │
│    ↓  UserPromptSubmit hook 触发（.claude/settings.json 注册）  │
│    ↓  每个 hook 按序跑 .claude/hooks/*_intercept.py             │
│    ↓  hook 读 stdin JSON → 正则匹配前缀                          │
│    ↓  命中: 输出 {"decision":"block","reason":"<回显>"} 后 exit 0│
│    ↓  未命中: exit 0（放行下一个 hook 或 LLM）                   │
│    prompt 不进模型 — reason 直接回显给 manager                   │
└─────────────────────────────────────────────────────────────────┘
```

**⚠️ 设计坑：hook 并没有共用 `dispatch`**。每个 hook 是独立 Python 文件，部分（stop/clear/health）通过 `import slash_commands` 调 `dispatch`；另一部分（tmux/send/compact/team/help/usage）有自己那一套前缀正则和执行逻辑（历史原因，hook 先写，dispatcher 后抽）。

- 优点：hook 可以独立升级、单独断点调试
- 缺点：改了 dispatcher 的行为，hook 侧不会自动跟上

扩展新命令时，**两边都要改**（见 §7）。

---

## 3. 命令清单（9 条）

| 命令 | 语法 | 示例 | 输出形态 |
|---|---|---|---|
| `/help` | `/help` | `/help` | 纯文本：命令总览 |
| `/team` | `/team` | `/team` | 卡片：所有 agent tmux 状态（emoji + 一行 brief） |
| `/usage` | `/usage` | `/usage` | 卡片：Claude Max 周额度 + Extra usage % 栅格 |
| `/health` | `/health` | `/health` | 卡片：主机 CPU/内存/磁盘 + 容器资源 + agent Top 9 |
| `/tmux` | `/tmux [agent] [lines]` | `/tmux devops 30` | 纯文本（群里包 code block）：capture-pane 尾部 |
| `/send` | `/send <agent> <msg>` | `/send devops 马上停` | 纯文本：注入结果 + 去向 |
| `/compact` | `/compact [agent]` | `/compact backend1` | 纯文本：在指定 agent 窗口敲 `/compact`；无参群聊默认 manager、hook 放行给 Claude 原生 |
| `/stop` | `/stop <agent>` | `/stop devops` | 纯文本：在目标 tmux 窗口 send `C-c` |
| `/clear` | `/clear <agent>` | `/clear devops` | 纯文本：`/clear` + 重送 hire init_msg（相当于远程 rehire，⚠️ 会丢会话记忆） |

所有「卡片」输出走飞书 `msg_type: interactive`，采用飞书 card **v1 schema**（`config / header / elements`）。群聊回显经 `feishu_msg.build_system_card` 包进「🛠️ 系统消息」灰色卡避免 bot 头像污染。

---

## 4. 文件布局（精确到行号）

> 原行号基线 commit `28fef3d` 已过时。以下改用函数名引用，用 `grep -n <函数名> scripts/<文件>` 定位。

### 4.1 `scripts/slash_commands.py`（~1600 行，主 dispatcher + 所有 handler）

> ⚠️ 行号基线已过时（原 954 行 → 当前 ~1600 行）。下表改用函数名引用，用 `grep -n` 定位。

| 函数/符号 | 作用 |
|---|---|
| `_load_agent_windows()` | 从 team.json 动态读取 agent 窗口列表 |
| `_host_session()` | 读 team.json 的 session 名 |
| `_run()` | subprocess 包装 |
| `_containers()` | 列 `claudeteam-*` docker ps |
| `_list_windows_local()` | 本机 tmux 窗口名 |
| `_container_window_map()` | 容器内 tmux 窗口 |
| `_send_local` / `_send_container` | tmux send-keys |
| `_HELP_TEXT` | 群聊帮助文本 |
| `_cmd_help` | /help handler |
| `_USAGE_*_RE` | 正则（解析 usage_snapshot.py 输出） |
| `_pct_color()` | 色阶阈值（80 红 / 50 橙 / 其他绿） |
| `_build_usage_card()` | /usage 卡片构造 |
| `_cmd_usage` | /usage handler（含 kimi/codex/gemini 分支） |
| `_cmd_tmux` | /tmux handler |
| `_cmd_send` | /send handler |
| `_cmd_compact` | /compact handler（含 hook/router 无参分歧） |
| `_parse_state()` | tmux 状态→emoji 映射（🔄/💤/⛔/⚠️/🗜️/❔/🛑/❓） |
| `_build_team_card()` | /team 3 列栅格 |
| `_cmd_team` | /team handler（本机 + 所有容器一起扫） |
| `_host_cpu` / `_host_mem` / `_host_disk` / `_docker_stats` / `_collect_agents` / `_collect_alarms` / `_collect_server_load` | /health 数据采集 |
| `_build_server_load_card()` | /health 卡片构造 |
| `_build_server_load_text()` | /health 纯文本（给 hook 用） |
| `_cmd_health` | /health handler |
| `_cmd_stop` | /stop handler |
| `_init_msg()` | hire 模板（对齐 `scripts/hire_agent.py`） |
| `_clear_local` / `_clear_container` | 两步：/clear + send init_msg |
| `_cmd_clear` | /clear handler |
| `_HANDLERS` | handler 注册列表（顺序即匹配顺序） |
| `dispatch(text)` | 主入口 → `(matched, reply)` |

### 4.2 `scripts/feishu_router.py`（group 入口）

| 行号 | 内容 |
|---|---|
| 21 | `import slash_commands` |
| 436–470 | **slash 前置过滤块**（manager 收件前拦截）：match → 回显卡 → `return`，绕过所有 agent 路由 |
| 440 | `matched, reply = slash_commands.dispatch(text)` |
| 453–466 | 卡片 vs 文本分支：`reply` 是 dict 且有 `card` → 直发；否则包进 `build_system_card` |

### 4.3 `.claude/hooks/*_intercept.py`（manager 本机入口）

9 个 hook 文件，两类：

**A. 直接 import `slash_commands.dispatch`（薄壳）**：
- `stop_intercept.py`（46 行）
- `clear_intercept.py`（46 行）
- `health_intercept.py`（48 行）

**B. 独立实现（历史原因，hook 先行）**：
- `help_intercept.py`（47 行）— 内置静态文本
- `team_intercept.py`（158 行）— 自己扫 tmux + 容器
- `usage_intercept.py`（54 行）— 跑 `scripts/usage_snapshot.py`
- `tmux_intercept.py`（79 行）— 正则 + tmux capture
- `send_intercept.py`（133 行）— 自己扫 + send-keys
- `compact_intercept.py`（128 行）— 自己扫 + send `/compact`

所有 hook 共同约定：
```python
# stdin JSON: {"prompt": "...", ...}
# 命中 → stdout: {"decision":"block","reason":"<文本>"}
# 未命中 → exit 0（空输出）
```

### 4.4 `.claude/settings.json`（46 行）

`hooks.UserPromptSubmit[0].hooks` 数组按顺序列出 9 个 hook。顺序即执行顺序；一旦某个 hook 输出 `block`，后续 hook 不再跑。

### 4.5 `scripts/feishu_msg.py`（~1050 行）

> ⚠️ 行号基线已过时。下表改用函数名引用，用 `grep -n` 定位。

| 函数 | 作用 |
|---|---|
| `_lark_im_send` | 发群消息（文本/图片/卡片），默认 `--as bot` |
| `build_system_card(content, template="grey")` | slash 纯文本回显用 |
| `build_card(from_agent, to_agent, content, priority)` | 员工间通讯用（不在本系统范畴） |

### 4.6 `scripts/slash_smoke_test.py`

冒烟测试入口，通过 `feishu_router.handle_event(fake_event(...))` 走整条链路。每 2 秒注入一条 fake `/xxx`。**⚠️ 不要在 CASES 里放会真发 tmux key 的命令**（/send <agent> <msg> / /stop <agent> / /clear <agent>），员工窗口真的会收到。

---

## 5. `AGENT_WINDOWS` 白名单 — 已动态化

**现状（2026-04-25 更新）**：`scripts/slash_commands.py` 已改为 `_load_agent_windows()` 从 `team.json` 动态读取 agent 列表，不再硬编码。hook 文件中的部分白名单仍为静态副本，但群聊入口（主要路径）已完全动态。

**新增员工只需**：
1. 改 `team.json` 加一条 agent 元数据（emoji/color/role）
2. 群聊入口自动生效（`_load_agent_windows()` 每次 dispatch 都重读 team.json）
3. 跑 `scripts/slash_smoke_test.py` 验一把群聊入口
4. 手工 `/send <新员工> hi`（本机或容器）验 hook 入口

---

## 6. 卡片设计原则

### 6.1 布局：column_set + 等宽栅格

所有多列结构都是飞书 `column_set`，`flex_mode: "none"`，每 column `width: "weighted"` + `weight: 1`（`/usage` 用 2:3 因为标签 + 值宽度不同）。参考 `_build_team_card`：

```python
{
  "tag": "column_set",
  "flex_mode": "none",
  "background_style": "default",
  "columns": [
      {"tag": "column", "width": "weighted", "weight": 1,
       "elements": [{"tag": "markdown", "content": "..."}]},
      ...
  ],
}
```

**⚠️ 飞书 v1 schema 下 `column` 内部不能放 `action` 元素**（按钮/下拉）。这是当年 picker 卡翻车的坑之一。需要按钮栅格用 `action` 容器（`actions: [btn1, btn2, btn3]`）直接放在 root elements 里。

### 6.2 header template 颜色

| 卡 | template |
|---|---|
| `/team` | `blue` |
| `/usage` | `purple` |
| `/health` | `purple`（服务器负载） |
| 系统消息（slash 文本回显） | `grey`（`build_system_card` 默认） |

### 6.3 色阶阈值（usage / health）

`_pct_color(pct)` 统一：
- `>= 80%` → `red`
- `>= 50%` → `orange`
- `< 50%` → `green`

色通过 `<font color='...'>` 在 markdown 里着。

### 6.4 北京时间

所有给老板看的时间都是 UTC+8 + 标「北京时间」。`BJ_TZ = timezone(timedelta(hours=8))`，`datetime.now(BJ_TZ)`。

---

## 7. 扩展新命令的 6 步流程

以加一条 `/foo` 命令为例：

1. **`scripts/slash_commands.py` 加 handler**：
   - 定义 `_cmd_foo(text: str)`：不匹配返 `None`，匹配返 `str` 或 `{"text": ..., "card": ...}`
   - 要卡片就额外写 `_build_foo_card(data)` 返回 v1 schema dict
   - 把 `_cmd_foo` 加到第 931 行的 `_HANDLERS` 列表（顺序决定优先级，`/help` 先）
   - 更新 `_HELP_TEXT`（第 92 行）

2. **写 hook 文件 `.claude/hooks/foo_intercept.py`**：
   - 抄 `health_intercept.py` 或 `usage_intercept.py` 模板
   - 薄壳版：`slash_commands.dispatch(prompt)` 然后 `print({"decision":"block","reason":...})`
   - 或独立实现（性能/隔离考虑时）

3. **`.claude/settings.json` 注册 hook**：在 `hooks.UserPromptSubmit[0].hooks` 数组末尾 append 一条：
   ```json
   {"type": "command",
    "command": "python3 $CLAUDE_PROJECT_DIR/.claude/hooks/foo_intercept.py"}
   ```

4. **更新 `.claude/hooks/help_intercept.py` 的 `HELP_TEXT`**：静态字符串，加一行 `/foo` 说明。

5. **`scripts/slash_smoke_test.py` 加用例**：
   - `CASES` 数组加 `"/foo"`（和 `/foo <参数>`）
   - **禁止**放有真副作用（真 tmux send、真 clear）的变体

6. **验收**：
   ```bash
   # 冒烟（走 router 假事件）
   python3 scripts/slash_commands.py  # 无 main，但可 python3 -c 测
   python3 scripts/slash_smoke_test.py

   # hook 冒烟
   echo '{"prompt":"/foo"}' | python3 .claude/hooks/foo_intercept.py
   ```

---

## 8. 部署 / 重启流程

| 改动对象 | 是否需重启 | 生效时机 |
|---|---|---|
| `scripts/slash_commands.py` | ❌ 不用 | `feishu_router` 每次调 `dispatch` 都现场 import，下一条 `/xxx` 就吃新代码；hook 同理，下次 UserPromptSubmit 就吃 |
| `scripts/feishu_router.py` | ✅ 要 | `kill <router python pid>`（宿主机: `cat scripts/.router.pid`；Docker 容器内: `cat /run/claudeteam/.router.pid`）— watchdog 30–60s 内自拉 |
| `scripts/watchdog.py` | ✅ 要（且手工） | watchdog 的 `_lark_event_cmd` 是 module-level 常量，改了要 `kill watchdog pid`，再到 `server-manager:watchdog` tmux 窗口启（避免被 shell 结束） |
| `.claude/hooks/*.py` | ❌ 不用 | hook 每次 UserPromptSubmit 都 fresh `python3` 进程 |
| `.claude/settings.json` | ❌ 不用 | Claude Code 每次 prompt 都重读 settings |
| `team.json` | ❌ 不用 | router 有 mtime-based 热加载（`RouterState.reload_agents`） |

**安全提示**：改 router / watchdog 前务必看 `ps -ef | grep watchdog` — 可能有别的团队（team02、mobile-dev）也有自己的 watchdog 在跑共享同一 profile，别误杀。

---

## 9. 前车之鉴 — picker/form 卡尝试失败复盘

**时间**：2026-04-20 03:50 – 04:30 北京时间
**回退 commit**：`50f654a` / `b6b3cf5` / `29077c6` / `60c2e98`（全丢）

### 9.1 想做什么

把以下无参斜杠从「usage text / 默认值」改成**交互卡片**：

- `/tmux` `/compact` `/stop` `/clear` → **picker**：飞书 button grid，老板点按钮选员工
- `/send` → **form**：agent 下拉 + 优先级 + 输入框 + 提交按钮

目标是把飞书交互能力当「HID」用，省掉老板打命令的动作。

### 9.2 失败链（按发现顺序）

1. **v1 schema 拒 form**：`_build_send_form_card` 用了 `{"tag": "form", "elements": [...]}`，v1 schema 下 form 不是合法容器 → 群里卡片只有 header 空白渲染
2. **event-type 名字错**：订阅用了 `card.action.trigger_v1`（照抄 `im.message.receive_v1` 惯例），实际应为 **`card.action.trigger`（无 `_v1` 后缀）**。lark-cli binary 里 SDK handler 是 `OnP2CardActionTrigger`（P2 = schema v2）
3. **v1 → v2 升级**：form 改为 `{"schema": "2.0", "config": ..., "header": ..., "body": {"elements": [...]}}`，cardkit API 接受，老板群里渲染正常
4. **点击报 200530**：飞书 card 子系统特有错误码（不在通用错误表里）。典型含义「schema 校验失败 / 提交结构非法」。加 `element_id` + 调整 button 结构后绕过
5. **真正的根因 — 回调不回**：lark-cli `event +subscribe --event-types card.action.trigger` 虽然在 WebSocket 上**能收到** card click 事件，但飞书 backend 在「交互卡片回调」通路里**不把这条 WS 长连接识别成『回调接收方』**。后端仍期望 URL 回调（配置里那种 HTTP callback endpoint），URL 上没人监听就报「target callback not online」或类似错误
6. `value.get("action")` 读到 `None` — v2 按钮的 callback value 被 Feishu 剥离 / 结构变更，handler 走到「未知 card action」兜底

### 9.3 结论

- **短期别做交互卡片**。picker/form 在飞书里要跑通需要正儿八经的 App 后端 HTTP endpoint（不是 WS），ClaudeTeam 当前架构没这个，临时 bridge 工程量很大
- **要做必须先搞清楚**：飞书新「卡片回传交互」通路怎么和长连接 event subscription 绑定。可能需要：
  - 等 lark-cli 出新版显式支持（当前版 subscribe 命令把 card.action.trigger 当普通 event，不告诉后端「本通路接收回调」）
  - 或者直连 `msg-frontier.feishu.cn/ws/v2` 用 Feishu SDK 发 callback-registration 帧
  - 或者搭一个最小 HTTP 服务器挂公网/内网穿透接收 URL 回调
- **不是不能做，是得先定通路再开工**。picker/form 卡的 card JSON 本身（schema 2.0）是跑通了的，只是点击通路接不回来

### 9.4 给维护团队的忠告

- 推交互卡前先让老板在 dev console 里配一个 **URL 回调地址**，用最简单 `{"action":"x"}` 按钮跑通一次 HTTP 回调
- 跑通 HTTP 通路后再决定要不要改 WS 通路（可能完全不需要）
- 单测 handler 时 **必须 monkeypatch `slash_commands.dispatch`**，否则本地跑单测会真往员工 tmux 敲 C-c / `/send` 消息（上轮就被坑到过）
- 本地 `python3 -c "..."` 直接调 `_build_*_card(...)` → `feishu_msg._lark_im_send(chat, card=...)` 可以只测渲染不走点击链，先把 UI 过一遍
- 飞书 `cardkit` API（`/open-apis/cardkit/v1/cards`）能 server-side 校验卡片 JSON 并返 `card_id`，比用 `messages-send` 迭代快

---

## 10. 附：调试 cheat sheet

```bash
# 手工触发 dispatch 不走飞书
python3 -c "import sys; sys.path.insert(0,'scripts'); \
  import slash_commands as s; print(s.dispatch('/health'))"

# hook 单点
echo '{"prompt":"/tmux devops 5"}' | python3 .claude/hooks/tmux_intercept.py

# 冒烟全链（注意会真发 feishu 群消息）
python3 scripts/slash_smoke_test.py

# 看 router 最近回显
tail -30 scripts/.tmux_intercept.log

# 列所有 slash handler
grep -n "^def _cmd_" scripts/slash_commands.py
```

---

*文档版本：v1（交付 baseline 28fef3d）· 作者：toolsmith*
