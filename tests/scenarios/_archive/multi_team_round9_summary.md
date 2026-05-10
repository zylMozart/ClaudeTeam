# #9 multi-team-same-container — Round summary

> 工作分支 `feat/multi-team-same-container`（5 commit on 4c01976），全程
> 本地不合 main、不 push remote。落地的能力 + 路上踩的坑 + 修复方案
> 与未来 follow-up 的速查档案。后续 reviewer / 新员工接手 multi-team
> 部署或追卡点 1 lark-cli ws bug 时先翻这一篇。

## 任务起点

老板派 #9（高优）：让两个 ClaudeTeam 部署同一容器并行跑，互不串扰。
后续 manager 在 #9 主体 done 之后追加"真群聊端到端验证基建"任务，
最终目标 = 老板加新群被动观看 team B 完整链路滚动演示。

## commit 时间线（不含 summary 自身）

> 本 summary 是 commit #6 (07709c0)，是 meta 档不在下表里；本表只列
> 落地能力 + 直接配套 doc 的 5 commit。后续 CR follow-up commit 也
> 不进表（避免动来动去），整体 commit 数以 `git log --oneline 4c01976..` 为准。

| SHA | message | 性质 |
|---|---|---|
| `91ad676` | feat(multi-team): 同一容器并行跑两套 ClaudeTeam 不再互窜 | 主体（5 处隔离改） |
| `b84e03c` | test(multi-team): 补容器默认路径回归网 | CR follow-up（_DATA_WRITABLE=True 补测） |
| `9c41279` | feat(multi-team): pane env 透传 PYTHONPATH (real-chat smoke fix) | 真群聊基建中发现 manager_b 撞 /tmp 共享 cache 后修 |
| `deb3553` | docs(scenarios): 真群聊端到端验证手册 (multi_team_e2e_smoke) | 复跑手册 + 5 坑诊断 |
| `a80f2a5` | feat(scenarios): bundled multi_team_e2e_canary.py runner | 老板 demo runner，60s drip 模拟消息 |

## #9 隔离改动 5 处（91ad676 主体）

| 维度 | 改动 |
|---|---|
| state_dir | `CLAUDETEAM_STATE_DIR` env 已支持，验证 |
| agent_home | 新增 `CLAUDETEAM_AGENT_HOME_BASE` env (lifecycle / claude_code adapter) |
| toml | 新增 `CLAUDETEAM_CONFIG_FILE` env 路径切换 |
| tenant token cache | `feishu/lark.py` `_TENANT_TOKEN_CACHE` 从 hardcoded `/tmp/...` 改 state_dir-relative |
| pane env 透传 | `lifecycle._PROPAGATED_ENV` 加 CLAUDETEAM_CONFIG_FILE / AGENT_HOME_BASE，9c41279 续加 PYTHONPATH |

## 卡点 1 诊断历程（"老板 app 配置" → 真根因）

### 时间线

1. **初症**：team B router 启动后 `+subscribe` 子进程 alive 但 0 events，
   循环 600s stale 自杀重启
2. **首推断**：team B app 飞书后台事件订阅未开（**与老板提示矛盾**）
3. **manager 反推**：4 项排查（bot 在群 / app 配置 / router env / 跨 team 真发）
4. **跨 team 真发证据**：用 team B owner 把 team A bot add 进 team B 群，
   team A bot urllib 发消息（sender=team A app ≠ team B 自己）— 仍 0 events
5. **bonus 误诊**：(5) admin scope endpoint 报"应用尚未开通"被误读为
   im scope 缺；老板正确指出 admin scope ≠ im 订阅
6. **第二轮调查**：升 lark-cli 1.0.26 → 1.0.27 仍 0 events；catch-all
   25 种事件类型也 0；lark-cli 自报 `Connected to Lark event WebSocket`
   + `Listening for: im.message.receive_v1` ws 客户端层 OK
7. **铁证**：扫 team A router.log 619 行历史 → **0 条 ws 实时事件痕迹**，
   全部 `subscribing → catching up N missed messages → no events 600s 自杀`
   循环。team A "能跑冒烟测试" = 实际靠 catchup HTTP pull 凑合在跑
8. **真根因**：lark-cli 1.0.26/1.0.27 在本容器 ws subscribe 协议层 broken；
   两个 app 都不 work；catchup HTTP pull 仍 work 所以业务功能没 100% 死

### 关键诊断陷阱（避免后人重踩）

- 单看 `+subscribe` 子进程 alive ≠ ws 真在收事件
- lark-cli 自报 "Connected" 只是 ws 握手成功；server 端是否真推取决于其它
- catchup pull 与 ws subscribe 是**两条独立路径**；只看 inbox 有消息不
  代表 ws work
- 单 app 0 events 不能下结论，必须 cross-app 对比
- pane shell pid env vs claude pid env 是两套（已落 e2e_smoke.md §4 坑诊断）

## 修复方案 ABC

| 方案 | 描述 | 当前状态 |
|---|---|---|
| **(a)** | router stale_event_threshold_s 600 → 60，靠 catchup 每分钟 pull | ✅ 已对 team B 落地（/data/claudeteam-b.toml hotfix），延迟 ≤60s |
| **(b)** | Python websockets 直连飞书 ws endpoint，绕 lark-cli | 待评估，需读 lark-cli SDK 源码 (fpid/aid/access_key/ticket negotiate) |
| **(c)** | upstream lark-cli github issue + 等修，1.0.27 已被验证仍 broken | 未提 |

team A 仍跑默认 600（产线稳态优先，不动）；team B 60s hotfix 配
canary runner 给老板看 ≤60s 节奏的滚动 demo。

## canary runner 用法（a80f2a5）

```bash
# 启动 (默认 60s 间隔, 20 条上限 = 20min 自动停)
python3 tests/scenarios/multi_team_e2e_canary.py

# 自定参数
python3 tests/scenarios/multi_team_e2e_canary.py --interval 30 --max 100

# 早停
touch /tmp/multi_team_canary.stop
# 或 kill <pid>
```

借 team A bot creds（从 /proc/<team A watchdog pid>/environ 读取，零 echo）。
sender 是 team A app → 走 team B 的 im.message.receive_v1 路径
（虽然 ws broken 但 60s catchup pull 接住）。

## 未来 follow-up（待办）

- [ ] **跟踪 lark-cli upstream**：1.0.27 ws subscribe 仍 broken 的 issue 复现 + 提报
- [ ] **如果 (b) Python ws 直连**：读 lark-cli SDK 源 (`@larksuite/cli/lib/.../events/ws.js` 之类)，复刻 negotiate；Python `websockets` 包替代
- [ ] **lark-cli 真修后**：`/data/claudeteam-b.toml [router].stale_event_threshold_s` 恢复 600；canary runner 仍可保留作 demo
- [ ] **#9 合 main 时机**：老板新规"改完先不合 main"，本分支 5 commit 候老板拍板再合
- [ ] **多于 2 团队**：本工作只验 N=2，N≥3 留作未来；锁、tmux server、agent_home 命名碰撞需重新审

## 一句话范围

> #9 = "同容器双 ClaudeTeam 隔离对照通过 + 真群聊端到端基建 + 卡点 1
> lark-cli ws bug 真根因诊断 + (a) 短期 hotfix 落地 + canary runner 给
> 老板看戏"。5 commit 在 feat/multi-team-same-container，本地不合 main。
