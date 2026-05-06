# Local message cycle (send → inbox → read)

## 场景
最基础的 agent 间通讯：send 写入 inbox，inbox 列出未读，read 标记已读后 inbox 不再列出。这条不依赖飞书 / tmux / 任何外部进程，是整个框架的"消息脊柱"基线。

## 范围
- 类型：host-only
- 凭证：none
- 操作员：boss / 任何脚本

## Given
- 已在某个目录 export 过 `CLAUDETEAM_STATE_DIR=$PWD/state`
- 该目录下没有 `state/facts/inbox.json`（或文件中 worker 未读为 0）

## When

```bash
claudeteam send worker manager "请处理 X 任务"
claudeteam inbox worker
# 复制 inbox 输出里的 local_id (msg_xxx) 给下一步
claudeteam read <local_id>
claudeteam inbox worker
```

## Then
1. **send** 退出 0，stdout 含 `sent → worker  [local_id=msg_xxx]`
2. **第一次 inbox** 退出 0，stdout 含 `📬 worker: 1 unread` 以及消息正文 `请处理 X 任务`
3. **read** 退出 0，stdout 含 `✅ marked read: msg_xxx`
4. **第二次 inbox** 退出 0，stdout 含 `📭 worker: no unread messages`
5. `state/facts/inbox.json` 存在，里面那条消息 `read: true` 且 `read_at` 非空

## 证据（执行时填）

```
- 命令: …
- T_send: …
- T_read: …
- 结果: pass | fail
- 后续: …
```
