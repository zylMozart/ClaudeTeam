# Round C — 真任务派发端到端（全自动）

## 目的

最完整的端到端烟测：脚本以用户身份在群里发一句任务 prompt，全程不再
人工介入，30-60 分钟后回来看群里是否出现 manager 的最终汇总卡。

通过即证明：**消息进入 → manager 拆任务 → 派 worker → worker 完工
回报 → R174 例外路由让 manager 看见 → manager 汇总 → 群里产出最终
答复**——这条 LLM 协作闭环工作。

不通过则说明从「基础设施」（路由、收件箱、pane 注入、say 反向）到
「prompt 工程」（manager 真懂得拆 / 跟 / 汇总）这一长链上断了某处。
具体哪段断要进失败排查段。

## 适用范围

- 跑前提：[host_smoke.md](host_smoke.md) §3-§7 已通过——基础设施不通时
  跑 round_c 没意义
- 时长：30-60 分钟（取决于任务复杂度与 LLM 响应）
- 凭证：用户 OAuth 已就绪（`lark-cli auth list` 有效）

## 前置条件

```bash
cd /path/to/ClaudeTeam
source .venv/bin/activate
export CLAUDETEAM_STATE_DIR="$PWD/state"
export LARK_CLI_NO_PROXY=1
export CLAUDETEAM_LARK_SEND_AS=bot
CHAT="$(python3 -c 'import json; print(json.load(open("runtime_config.json"))["chat_id"])')"

# 确认基础设施健康
claudeteam health   # 应全绿
```

## 操作（全自动）

```bash
ANCHOR="round-c-$(date +%s)"
TASK="@manager 测试任务 [$ANCHOR]：把当前 ClaudeTeam 的 README 翻译成英文，
存到 README.en.md。要求：
- 保留原结构和代码块
- 术语 (CLI / pane / chat) 不译
- 完成后由你汇总各 worker 的差异，给我一份 review 报告

请把任务拆成跟当前 worker 数对应的子块，分配给团队里每个 worker，
跟踪到完成。完成报告里务必带上 [$ANCHOR] 让我能定位到这次任务。"

LARK_CLI_NO_PROXY=1 lark-cli im +messages-send \
  --chat-id "$CHAT" --text "$TASK" --as user --format json | tail -1

echo "anchor=$ANCHOR"
echo "now=$(date)"
echo "wait 30-60 min then check pass criteria"
```

## 通过条件（看群里）

发出任务后 60 分钟内，群里**必须出现以下一张 manager 卡**：

1. 卡发出时间晚于 ANCHOR 时间
2. 卡内容含 `$ANCHOR` 字样
3. 卡内容长度 > 200 字（汇总报告不会太短）
4. 卡内容里能看到至少 2 个 worker 的名字（`worker_cc` / `worker_codex` / `worker_kimi` 之一以上）
5. 卡内容里能看到「review」「汇总」「translation」「diff」这类汇总性关键词
6. 仓库里 README.en.md 存在且非空（worker 真把翻译写到文件了）

自动化检查脚本：

```bash
TIMEOUT_MIN=60
DEADLINE=$(($(date +%s) + TIMEOUT_MIN*60))
while [ $(date +%s) -lt $DEADLINE ]; do
  HIT=$(LARK_CLI_NO_PROXY=1 lark-cli im +chat-messages-list \
    --chat-id "$CHAT" --as bot --page-size 30 --format json | python3 -c "
import json, sys
d = json.load(sys.stdin)
for m in d.get('data',{}).get('messages',[]):
    c = m.get('content','')
    if '$ANCHOR' in c and 'manager' in c.lower():
        if len(c) > 200 and any(w in c for w in ('worker_cc','worker_codex','worker_kimi')):
            print('HIT', m.get('message_id'))
            break
")
  if [ -n "$HIT" ]; then echo "PASS: $HIT"; break; fi
  sleep 60
done
test -s README.en.md && echo "README.en.md exists, $(wc -l < README.en.md) lines"
```

## 失败排查

最终通过条件不满足时，按这条链定位断点：

| 现象 | 可能问题 | 怎么查 |
|---|---|---|
| 群里没看到任何 manager 卡 | 任务消息根本没进 manager pane | `claudeteam inbox manager`；如果有 anchor 这条，但 pane 里 `tmux capture-pane -t ClaudeTeam:manager -p \| tail -30` 没动，是 pane 注入失败 |
| manager 卡有但只是 ack | manager LLM 没拆任务 | manager identity prompt 不够明确——这是 prompt 工程问题，不是 router bug |
| 群里只看到 manager 派单卡，没 worker 回报 | worker 收件箱没拿到派单，或 worker pane 卡住 | `claudeteam inbox worker_cc`；`claudeteam peek worker_cc 30` |
| 群里 worker 报了完工，但 manager 没出汇总卡 | R174 反向路由分支没生效 | `claudeteam inbox manager` 看 worker 回报有没有路回；如果没路回，看 `state/router.log` 找 `_card_sender_agent` 解析 |
| 60 分钟超时 | LLM 思考慢 / quota 限速 / 卡在 reidentify | `claudeteam team` 看每个 agent 状态；`claudeteam usage` 看是否限速 |
| README.en.md 不存在 | worker 真没干活，只在群里说完成了 | `claudeteam peek worker_cc 50` 看是否真跑了 Write 工具 |

## 已知风险

1. **manager 拆任务质量飘**：LLM 的拆分 / 汇总质量每次不一样。这条烟测
   只验"manager 真在试图汇总"，不强求每次质量都一致。如果连续多次跑
   出现汇总质量差，回去改 manager identity prompt 而不是 router 代码
2. **worker 用 `send` 写收件箱而不是 `say` 发群**：Round B G5.a 留下的
   LLM 行为问题。如果 worker 完工后 manager 一直不汇总，多半是 worker
   只 send 没 say，导致 R174 反向路由没触发。看 manager 收件箱有没有
   from=worker 的行——有则 R174 OK，没有则是 worker 的 prompt 问题
3. **kimi 配额 429**：如果你的 team 含 worker_kimi，可能因为 quota
   卡住。验通过条件 #4 时只要求出现 ≥2 个 worker 名字，不强求 kimi
4. **macOS host：claude 凭证可能过期**——长跑期间如果 worker 凭证过期，
   pane 会显示 "Not logged in"。临时解：`claudeteam down && claudeteam up`
   重新从 keychain 物化凭证，再重发任务（带新 anchor）

## 不在范围

- **真改代码 + 真 git push**：本剧本任务限定在文档/翻译类，避免 worker
  真 push。代码 PR 类放 Round D
- **多任务并发**：本剧本只发一个任务，看 manager 调度多任务放 Round D
- **跨群任务**：所有 say 都在同一个群

## 记录（跑的时候填）

```
- ANCHOR: …
- 任务发出时间 T_send: …
- 第一张 manager 卡时间 T_first: …  延迟: …s
- 第一张 worker 派单卡时间: …
- 第一张 worker 完工卡时间: …
- 最终汇总卡时间 T_final: …  从 T_send 起累计: …min
- README.en.md 行数: …
- 通过 / 失败: …
- 备注: …
```
