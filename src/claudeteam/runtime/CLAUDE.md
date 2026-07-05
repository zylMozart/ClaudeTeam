# runtime/ — 进程、路径、配置、两种 agent 运行时

## 地图（先找对文件再动手）

| 文件 | 管什么 |
|------|--------|
| paths.py | 一切磁盘路径；全部从 `$CLAUDETEAM_STATE_DIR` 现算 |
| config.py | claudeteam.toml [team] / 兼容旧 team.json；`agent_runner()` 在这 |
| tunables.py | 参数级联：env > toml > 代码默认（`tunable("a.b", 默认)`) |
| agent_auth.py | 凭据解析 token > login > api_key；密钥永不进 shell 明文 |
| lifecycle.py | pane 供给（tmux 路径）+ acp viewer pane + spawn env 文件 |
| tmux.py / wake.py / pane_probe.py | tmux 运行时三件套（薄包装/唤醒/抓屏探测）|
| acp.py | ACP JSON-RPC stdio 客户端（协议层，无业务）|
| acp_host.py | ACP 运行时：per-agent worker 消费队列、turn 生命周期、transcript |
| standup.py | 定时进度巡视（活跃期每 N 分钟让 manager 汇报）|
| watchdog.py | 守护进程保活（只管 router；agent 保活各 runner 自理）|

## 铁律

- **路径/配置不缓存**。`state_dir()`、`config.*` 每次调用现读——
  测试隔离和"改 toml 即生效"都靠这个。在模块顶层存
  `STATE = state_dir()` 是本 repo 的经典错误。
- **副作用可注入**。任何 subprocess / 文件 / sleep 都留 `run=` /
  `popen=` / `sleep=` / `now=` 参数，默认值指向真实现。
- **tmux 系（wake/pane_probe/tmux.py）只服务 runner="tmux" 的 agent**。
  给 acp agent 加抓屏逻辑 = 走回头路；acp 的状态从队列 + pid 文件读
  （`acp_host.probe`）。

## ACP 运行时心智模型（改 acp*.py 前必读）

- 队列（store/acp_queue）是投递状态机：任何进程都能 enqueue，
  **唯一消费者**是 router 进程里的 AcpHost worker 线程。
- 至少一次投递：host 崩溃时 in-flight 行会被下一个 host `recover_stuck`
  重臂重跑；所以 turn 必须容忍重放（幂等性由 LLM 语义兜底，别在
  这里做去重——去重是 router 层 msg_id 的活）。
- 每个 fresh session 先跑 identity turn 0，**成功后才持久化 session.json
  和消费队列**——identity 失败的 session 必须整个作废（否则 session/load
  会永远恢复一个不知道自己是谁的 agent）。session/load 成功则跳过
  identity（上下文还在）。
- cancel 必须能打断 in-flight turn → 它走 host 级 control 线程，
  不走被阻塞的 worker 线程。这个分工别破坏。
- worker 花名册跟随 LIVE roster（control 线程定期 _sync_workers）——
  运行中 hire/fire/改 runner 都会生效，别改回启动时冻结。
- claimed 行必须 settle 或 requeue，无一例外（worker 循环有兜底
  try/except 就是为这个）；起不来的 agent 满 MAX_ATTEMPTS 停放
  FAILED + 阻塞 status，不许 pending↔prompting 无限乒乓。
- `/shutdown` 靠 `pause_all()`（state/acp/paused 标记文件）让 worker
  蛰伏——只杀 tmux session 关不掉住在 router 里的 ACP agent。`up`
  无条件 resume。
