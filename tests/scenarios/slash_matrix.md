# 斜杠命令矩阵（rebuild/minimal 分支）

本分支冒烟测试的斜杠命令验收清单。命令面与主分支保持一致的 9 条
（R172.b 之后老板要求去掉了 `/recall` 与 `/forget`），加上 rebuild
专有的「按 CLI 分组」`/usage` 卡（R170）和不再用 column_set 的渲染
（R172.b——飞书当前会把 column_set 折叠掉）。

触发器一律是真实用户在绑定群里发消息——**斜杠必须出现在消息开头**。
`@bot /team` 不会触发，单独的 `/team` 才会。在单元层面用
`subscribe.process_lines` 喂构造事件做 handler 覆盖也可以，但要在
跑日志里写明「构造事件」，这种方式不证明 lark-cli +subscribe 长连接
路径是通的。

## 只读命令

| 命令 | 群里期望结果 | 通过标准 | 失败标准 |
| --- | --- | --- | --- |
| `/help` | 列出所有 `/<命令>` 的卡 | 列全 9 条：/help /team /health /usage /tmux /send /compact /stop /clear | 缺命令；返回的是纯文本而不是卡 |
| `/team` | 卡片，每个 agent 一行 `<emoji> **<名字>**: <摘要>` + 汇总 | 每个团队成员渲染成（💤 空闲 / 🔄 工作中 / ⏸ 懒启动 / ⚠ 等权限 / 🛑 挂了 / 🔘 未知）之一。全健康时卡头绿色，含 ⚠/🛑/❌ 时黄色；懒启动 agent 显示 ⏸ 而非 🛑 | 漏 agent；会话名错；懒启动 agent 显示成 🛑（R129/R144 回归） |
| `/health` | 富卡，含「🖥️ 主机总览」+「👤 员工细分」分段 | CPU / 内存 / 磁盘 行有数据（容器里走 procps，macOS 主机走 /proc 直读兜底）；逐个 agent 显示 CPU% 与 RSS（基于 pane PID 子树的 ps walk）。无告警时卡头紫色，有告警时黄色 | ps/uptime 都在却显示「无数据」（procps 缺失回归）；agent 的 CPU 显示 0/0（pane PID 解析坏了） |
| `/usage [视图]` | 卡片，三段：Claude Code（ccusage）/ Codex（JWT）/ Kimi（api.kimi.com） | 每个指标渲染成 `**标签**：值` 单行 markdown（column_set 已坏，R172.b 删除）。Codex 段读 `~/.codex/auth.json` 里的 id_token；Kimi 段读 `~/.kimi/credentials/kimi-code.json` 并打 HTTPS | 段落缺失；标签和值上下堆叠对不齐（column_set 回归） |
| `/tmux [agent] [N]` | 卡片，agent pane 最近 N 行（默认 10，最大 2000） | 内容是带围栏的代码块——等宽显示、缩进保留。无 agent 参数时默认 manager | 围栏被当作字面文本；选错 pane；超过 N 行被截断 |

## 状态变更类命令

下面这些会真改活的 CLI 状态——只在你能容忍副作用的目标上发，跑前跑后都把
pane 状态截下来对比。

| 命令 | 期望 | 风险 | 通过 |
| --- | --- | --- | --- |
| `/send <agent> <消息>` | 在 agent pane 上执行 `tmux send-keys` + 回车 | 跳过懒启动与收件箱，纯粹原始注入。只用一次性废文本测 | pane 收到一次、不重复、不污染 shell |
| `/compact [agent]` | 注入 `/compact`，45 秒后再触发一次身份重新注入 | 目标 pane 会进入长时间的对话压缩 | 一次 compact 落地，settle 之后身份重读触发 |
| `/stop <agent>` | 给 agent pane 发 `C-c` | 打断在做的事 | 当前操作被打断，pane 仍可用 |
| `/clear <agent>` | 注入 `/clear` 然后重新初始化（等价 hire 形态） | 丢掉 CLI 对话上下文 | 一次 /clear + 一次 init 消息，无 shell 污染 |

## 路由分类（无斜杠的普通文本）

斜杠命令是 router 在分类阶段就拦截的零 LLM 路径。另一条 router 要走的
路径是 **classify → deliver → 收件箱 + tmux 注入**，对应人话和
agent 之间的对话。下面这些用例证明 lark 长连接 → router → store/local_facts
+ runtime/tmux 的整链路是通的。

### R174「manager 是唯一接口」契约

R174（提交 `9e43309`）改动了路由根本规则：**所有人话不论加不加 @ 都
只路由到 manager**。`@worker_cc` 此时是 manager 看见的文本内容，不是
路由指令；`@team` 与 `全体X` 也不再分流到多个 worker。

对应的契约：人 → manager。manager 自己决定要不要再用 `claudeteam send`
派给某个 worker。这把"消息分发"从 router 抬到了 manager 的 prompt 层。

| # | 触发 | 期望 |
| --- | --- | --- |
| R1 | 老板在绑定群里说「开发一个登录页」（无 @ 也无 `[` 前缀） | manager 收件箱新增一行（sender=user，content=原文）；manager pane 经 `lifecycle.wake_if_dormant` 收到正文 + 回车；状态翻成「进行中」 |
| R2 | 老板说 `[boss] /team`（manager 风格的发件人前缀） | router 先剥掉 `[boss]`，把 `/team` 当斜杠分发；机器人的回复卡落进群。**回归点**：`[boss] /team` 不能被当成普通话路由进 manager（A2/B1 round 的回归） |
| R3 | `@worker_cc 你看下 README` | manager 收件箱拿到这一行，文本带原始 `@worker_cc` 前缀；worker_cc 与 worker_codex 收件箱**不动** |
| R4 | `@worker_cc @worker_codex 都过来` | 同 R3——manager 一行，worker 们不动 |
| R5 | `@unknown_agent hi`（错拼名字） | manager 收一行；router 不解析未知 @，原样进 manager |
| R6 | `@team 全员同步进度` | manager 收件箱多一条；router 日志显示 `action=route targets=[manager]`，不是 `BROADCAST` |
| R7 | `全体注意，今晚 18:00 review` | 同 R6 |
| R8 | 单纯 `@team` 无正文 | manager 收一行空内容；R174 之前会广播给所有 agent，现在不会 |

### R174 的例外分支：worker → manager 反向路由

R174 还加了一条对称设计：worker 自己发的卡（机器人身份发出，但卡片头
解析得到 worker 名字）会被 router 路回 manager 的收件箱。这样 manager
能看到 worker 在群里说了什么并做汇总。manager 自己发的卡仍然会被
DROP `bot_self`，避免回声循环。

| # | 触发 | 期望 |
| --- | --- | --- |
| W1 | 在 worker_cc pane 里跑 `claudeteam say worker_cc "auth 模块完成"` | 群里出现 worker_cc 的绿色卡；router 把这条卡路由到 manager 的收件箱（sender=worker_cc）；manager pane 处理这条新收件箱 |
| W2 | 同上但 sender 是 manager（`claudeteam say manager "..."`） | 群里出现 manager 的卡；**没有任何 agent 收件箱**新增（router DROP `bot_self`） |

### Agent 之间互发（peer messaging）

下面这些跑在 agent pane 内部，验证 `claudeteam send`：

| # | 起点 pane | 命令 | 期望 |
| --- | --- | --- | --- |
| P1 | manager | `claudeteam send worker_cc manager "查 auth 模块"` | worker_cc 收件箱多一行（from=manager, to=worker_cc）；pane 收到正文。manager pane 不变 |
| P2 | worker_cc | `claudeteam send manager worker_cc "auth 用 bcrypt"` | manager 收件箱多一行（from=worker_cc, to=manager）；manager pane 收到。**回归点**：from=worker_cc 不能被错当成跨团队或自言自语而 DROP |
| P3 | worker_cc | `claudeteam send worker_codex worker_cc "看一下 token 过期处理"` | worker_codex 收件箱新增一行；pane 收到。peer-to-peer 不会镜像到 manager |
| P4 | manager | `claudeteam say manager "进展同步" --card` | 群里出现 manager 蓝色卡头的 v2 markdown 卡；老板 + workers 在群 UI 里都能看到。**这条不写任何收件箱**——`say` 是「发到群里说一声」，不是「派单进收件箱」 |

### 可见性与汇报

老板要求的「manager 看得到 worker 说了什么」：

| # | 准备 | 期望 |
| --- | --- | --- |
| V1 | worker_cc 跑 `claudeteam say worker_cc "完成 auth 模块" --card` | 群里出现 worker_cc 卡（按 worker_* 配色绿色卡头）。**manager 收件箱也会拿到这条**（W1 反向路由分支）；manager 通过收件箱或 `claudeteam peek worker_cc` 都能看 |
| V2 | manager 在自己 pane 里跑 `claudeteam peek worker_cc 30` | 输出是 worker_cc pane 缓冲最近 30 行；和从群里发 `/tmux worker_cc 30` 等价但少一次往返 |
| V3 | 全员汇报：老板说 `@team 报告状态`；每个 worker 通过 `claudeteam say <自己> "<状态>" --card`；manager 跑 `claudeteam team` 看 | 每个 worker 都展示「进行中」或「已完成」；群里每个 worker 一张卡，老板能扫卡或 `/team` 看汇总 |

### 收件箱审计

| # | 步骤 | 期望 |
| --- | --- | --- |
| I1 | 老板说 `@worker_cc test`；R174 后这条到 manager 收件箱。再让 manager 通过 `claudeteam send worker_cc manager test` 转给 worker_cc，然后在 worker_cc pane 里 `claudeteam inbox worker_cc` | 看得到 from=manager 的一行 test |
| I2 | worker_cc 跑 `claudeteam read <local_id>`，再 `claudeteam inbox worker_cc --unread` | 那一行从未读列表消失 |
| I3 | 老板快速说 5 条话；看 manager 收件箱 | 5 行都在，按 created_at 排序；router.seen_msg_ids 是按 message_id 去重的，不会因为内容相同就丢 |

## 容器部署的前置（R161）

容器里的 lark-cli 触不到 macOS keychain，bot 身份会失败 `[10003] invalid param`，
除非容器（一般通过被 gitignore 的 `.env` + docker-compose）设了：

- `FEISHU_APP_ID`（或 `LARKSUITE_CLI_APP_ID`）
- `FEISHU_APP_SECRET`（或 `LARKSUITE_CLI_APP_SECRET`）

R161 的 `feishu/lark.subprocess_env` 会用 app-id/secret 在第一次调用时
拉一份 tenant_access_token 缓存到 `/tmp/claudeteam_tenant_token.json`
（约 77 分钟，到期前 60 秒自动续）。容器里不需要手动 `lark-cli auth login`。

如果两个变量都没有，且 keychain 也不可达（Linux 容器无 env），
`subprocess_env` 返回原 env，让 lark-cli 自己抛 `no access token available for bot`。

## 本机部署的前置（macOS）

本机部署不走 R161 容器路径，而是依赖 macOS keychain。两套机制：

1. **bot 凭证**：keychain 里 service `lark-cli-credentials`，account `<app_id>`。
   首次部署 `lark-cli config init` 写入即可
2. **user 凭证**（用于 `--as user` 模拟用户发消息）：keychain 里 service
   `lark-cli-credentials`，account `<user_open_id>`。设备授权流程见
   [host_smoke.md](host_smoke.md) 第 2 节
3. **claude 凭证**：keychain 里 service `Claude Code-credentials`。
   `runtime/lifecycle._ensure_claude_agent_home` 在 macOS 分支下用
   `security find-generic-password -w` 把它物化到每个 agent home 的
   `.credentials.json`（提交 `780fd08`），让带 HOME 隔离的 pane 也能登录

## 每条命令要记的证据

每条都跑过之后记一下：

- 触发方式：真实用户事件还是进程内构造事件（`subscribe.process_lines`）
- 机器人回复卡的 lark message_id
- 用户消息到机器人回复的延迟
- 卡头颜色 + body 元素数量（健康度判断）
- 如果 lark-cli 调用非 200，记错误码
- `/tmux` 专门记一下：把渲染出来的 body 头三行贴出来，让下一个审阅者一眼看出
  v2 markdown 是渲染成代码块还是回退成字面三反引号
