# Identity render: per-agent identity.md

## 场景
每个 agent 起来后能从 `$CLAUDETEAM_STATE_DIR/agents/<name>/identity.md` 读到属于自己的简介——名字、角色、跑在哪个 CLI、`claudeteam send / say` 的参数顺序契约。Manager 跟 worker 用不同的模板（前者管派单，后者管干活）。`hire` / `start` 在 spawn 完 pane 后写文件；`hire` 重跑会覆盖。

## 范围
- 类型：host-only
- 凭证：none

## Given
- `claudeteam` CLI 已安装
- 已有 team.json（含 manager + 至少一个 worker）
- `CLAUDETEAM_STATE_DIR=$PWD/state`

## When

```bash
claudeteam start
ls $CLAUDETEAM_STATE_DIR/agents/

cat $CLAUDETEAM_STATE_DIR/agents/manager/identity.md
cat $CLAUDETEAM_STATE_DIR/agents/worker_cc/identity.md

# re-hire to confirm overwrite
claudeteam fire worker_cc
claudeteam hire worker_cc
cat $CLAUDETEAM_STATE_DIR/agents/worker_cc/identity.md
```

## Then
1. `start` 后，每个 agent 在 `state/agents/<name>/identity.md` 都生成一个文件
2. **manager** 的文件含 `team manager` + `Receive messages from the boss` + `claudeteam send` ✅/❌ 段
3. **worker** 的文件含 `team worker` + `Pick up tasks` + `claudeteam send manager <name>` 示例
4. 文件里有 agent 的真实 `name / role / cli / model`（来自 team.json）
5. 重新 `hire` 同一个 agent 会覆盖文件内容（不是追加）
6. 没有 team.json 字段时回退默认（`cli=claude-code`，`model=""`，`role=<agent name>`）

## 反例
- 如果 `agents/<name>/` 目录不存在，`identity.write` 会自动 `mkdir -p`，不应报错
- `claudeteam hire <unknown>` 不会写 identity（因为它在 spawn 之前就被拒）

## 证据（执行时填）

```
- T_start: …
- 文件路径列表: …
- manager 文件首行: …
- 重新 hire 后内容是否覆盖: pass | fail
- 后续: …
```
