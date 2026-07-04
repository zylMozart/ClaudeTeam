# store/ — 文件型本地状态（本项目没有数据库，这里就是数据库）

## 文件与职责

| 模块 | 磁盘文件 | 语义 |
|------|---------|------|
| local_facts.py | facts/inbox.json | 每条路由消息的 canonical record |
| | facts/status.json | agent 自报状态（进行中/待命/已停止…）|
| | facts/heartbeats.json | 最近活跃时间戳 |
| | facts/logs.jsonl | append-only 审计（含 acp_turn ACK）|
| acp_queue.py | acp/<agent>/queue.json | ACP 投递状态机 |
| memory.py / team_memory.py | agents/<n>/memory.jsonl 等 | 持久记忆 |
| tasks.py | …… | 任务追踪 |

## 铁律

- **所有写入走 `util.write_json`（tmp+rename 原子写）+ `util.flock`**。
  直接 `open(...).write` 会在崩溃时留半截 JSON，下游全体读挂。
  jsonl 追加也要在 flock 里。
- **读方容忍脏数据**：jsonl 解析用 `util.read_jsonl`（静默跳过残行）。
- 状态字符串是协议：`已停止`（=retired，fire 写入、全体投递路径查询）
  这类值改一个字就会破坏跨模块契约，全局搜索后再动。

## acp_queue 的状态机（不变量，动之前想清楚）

```
pending → prompting → done | failed        (kind="prompt")
pending → done                             (kind="cancel"/"stop"，控制行即刻消费)
```

- `claim_next` 原子领取（标 prompting + attempts+1）；`settle` 是 ACK。
- host 重启时 `recover_stuck`：prompting 行 → 重回 pending（≥MAX_ATTEMPTS
  次则停放 failed）——这是"消息不丢"保证的实现点，删掉它 = 回到丢消息时代。
- 已结算行保留最近 KEEP_SETTLED 条（有界文件），审计长尾在 logs.jsonl。
- 新增行字段要向后兼容：读方全部用 `.get()`，禁止 KeyError 于旧行。
