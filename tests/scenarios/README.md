# tests/scenarios/ — 端到端冒烟剧本

冒烟测试的形态：**用户视角，从飞书群里发消息开始，到群里看到结果结束**。
中间过程（收件箱状态、pane 缓冲、router 日志）是诊断手段，只在失败时才看。

不要把"手动跑一遍 CLI 看输出对不对"塞进这里——那是单元测试已经覆盖的事
（`tests/unit/test_*.py` 用 mock 自动跑）。这里只放需要真飞书 + 真 tmux +
真 CLI 联动才能验证的剧本。

## 入口：刚部署完想 1 分钟过一遍

→ **[host_smoke.md](host_smoke.md)**

九节内容，全部以"群里发 → 群里看"为主轴：

1. 团队上线（`claudeteam up` + `health`）
2. 用户 OAuth（设备授权流程，一次性）
3. 9 条斜杠命令矩阵（含状态变更类）
4. 普通文本路由（验证 R174「manager 是唯一接口」）
5. Worker → manager 反向路由（R174 例外分支）
6. 路由器重启不丢消息（catchup）
7. 懒启动 worker（lazy 标记 + 首消息触发起 CLI）
8. 多部署冲突（同一 App 抢订阅锁的失败语义）
9. 收尾

这一篇是新部署默认入口，跑通即认定基础设施可用。

## 专题剧本

| 文件 | 范围 | 何时跑 |
|---|---|---|
| [slash_matrix.md](slash_matrix.md) | 9 条斜杠 + 路由分类的详细 pass/fail 标准、color 期望、错误诊断 | host_smoke 某条出错时翻这一篇 |
| [round_c_real_task.md](round_c_real_task.md) | 老板 → manager → workers → 汇总，30-60 分钟真任务派活 | 重大改动后或想压测协作时 |
| [reidentify.md](reidentify.md) | `claudeteam reidentify` 重新注入身份的几种触发情境 | post-compact 或 worker 记忆乱了 |
| [multi_team_same_host.md](multi_team_same_host.md) | 同一容器跑两套独立 ClaudeTeam（不同 App + 不同 chat），互不串扰的端到端验收 | 多团队部署上线前 |

## 归档：`_archive/`

20 篇早期文档移到 [_archive/](_archive/)，**不再当冒烟测试看**。它们仍有价值
但归类不对：

**单元测试已 100% 覆盖（mock 自动跑）**——这些剧本只是手抄一遍 unit test。
跑 `python3 tests/run.py` 就够了：

- `local_message_cycle.md` ← `test_local_facts.py` / `test_commands_messaging.py`
- `spawn_cmd_per_cli.md` ← `test_agents.py` / `test_agents_*.py`
- `identity_render.md` ← `test_agents_identity.py`
- `agent_status_and_audit.md` ← `test_commands_status_log.py`
- `task_lifecycle.md` ← `test_commands_task.py` / `test_store_tasks.py`
- `team_overview_and_workspace.md` ← `test_commands_team_workspace.py`
- `version_check.md` ← `test_commands_version.py`
- `init_bootstrap.md` ← `test_commands_init.py`

**操作手册（应在 `docs/` 不在 `tests/`）**——讲怎么用某个命令的步骤说明：

- `team_lifecycle.md`、`team_down_and_reset.md`、`team_switch.md`
- `health_check.md`、`usage_snapshot.md`
- `docker_deploy.md`
- `cards_memory_and_speed.md`（189 行 4 主题杂货铺）

**已被 host_smoke 吸收**——核心场景已抽到主入口：

| Archive | 吸收到 host_smoke 的哪一节 |
|---|---|
| `feishu_say_chat_send.md` | §5 Worker → manager 反向路由 |
| `router_event_to_pane.md` | §3、§4、§5（路由分类全覆盖） |
| `router_catchup.md` | §6 路由器重启不丢 |
| `lazy_wake.md` | §7 懒启动 worker |
| `team_switch.md` | §8 多部署冲突 |
| `orphan_subscribe_reap.md` | §8 同主题（watchdog 内部机制描述放 archive） |
| `reidentify.md` 的烟测面 | （保留独立专题，因为 post-compact 是常用 ops） |

## 命名规则

- 文件名 `<主题>.md`，全小写、下划线分隔
- 一篇 = 一个端到端可验证的目标，不是 N 个不相关主题的聚合
- 模板：`## 目的` / `## 适用范围` / `## 前置条件` / `## 操作` / `## 期望` / `## 失败排查` / `## 不在范围`
- **不要新建**「`_v2`」「`_round_X`」后缀文件——直接改原文件，git 历史保留版本

## 加一篇新剧本之前先问

1. 这条路径单元测试能 mock 出来吗？能 → 写 unit test 不写剧本
2. 这条是「教用户怎么用」吗？是 → 写到 `docs/` 不写在这
3. 必须真起 tmux + 真发飞书才能验吗？是 → 写在这；并且看能不能直接往
   `host_smoke.md` 加一节，避免新文件
4. 真要新文件——是不是 host_smoke 已经太长该拆？拆按"基础烟测 / 真任务"
   两栏，不要按"每个特性一篇"切

## 已知归档候选

冒烟价值 < 操作手册价值的会持续往 `_archive/` 沉。现在杂货铺
[cards_memory_and_speed.md](_archive/cards_memory_and_speed.md) 还在那，
里面 4 个主题（卡片、内存、watchdog 告警、lark 速度）应当拆到对应的
operations doc 或 design doc。
