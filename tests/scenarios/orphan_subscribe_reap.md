# Watchdog reaps orphan lark-cli `+subscribe` before respawn

## 场景

router daemon 通过 `subprocess.Popen(start_new_session=True)` 拉起一条
`npx → npm exec → node → @larksuite/cli +subscribe` 进程链。正常
退出（SIGTERM / KeyboardInterrupt）走 `_terminate_subscribe_group`，
killpg 清掉整个组。但 router 被 **SIGKILL / OOM / 内核崩溃** 干掉时，
SIGTERM 处理器跑不了，npm-exec 父进程 reparent 到 PID 1（macOS 是
launchd），变成"孤儿订阅"。

watchdog 检测到 router pid 死了，会 respawn 新 router → 新 router 又
开一条新订阅链。两个 subscribe 同时连飞书 → 飞书把事件**随机分给两边**，
新 router 只看到子集，消息会"丢一半"。

round-65 在 `runtime/watchdog.py` 加了 `reap_orphans(spec)`：respawn 前
扫 `ps -eo pid,ppid,command`，找 PPID=1 且命令行同时包含 `@larksuite/cli`
和 `+subscribe` 的进程，SIGTERM 掉。

本 playbook 验证这套机制对真实 SIGKILL 场景生效。

## 范围

- 类型：local-only（不需要飞书 chat 通道，纯进程行为）
- 凭证：无
- 操作员：开发者 / 部署者

## Given

- `claudeteam up` 已把 team + router + watchdog 都拉起来
- `claudeteam health` 显示 router/watchdog 都 alive
- `ps -eo pid,ppid,command | grep @larksuite/cli` 应该看到一条
  `npm exec @larksuite/cli ... +subscribe ...`，PPID 是 router 的 pid

## When — 模拟 router 被 SIGKILL

```bash
# 1. 记录当前订阅链 root pid
ps -eo pid,ppid,command | grep '@larksuite/cli.*+subscribe' | grep -v grep
# 假设输出 PID=11111, PPID=router_pid (例如 22222)

# 2. SIGKILL router（绕过它的 SIGTERM cleanup）
kill -9 $(cat $CLAUDETEAM_STATE_DIR/router.pid)

# 3. 立即看 ps —— router 没了，订阅链 PPID 应该跳到 1
ps -eo pid,ppid,command | grep '@larksuite/cli.*+subscribe' | grep -v grep
# 期望: PID=11111 PPID=1 ... npm exec @larksuite/cli ... +subscribe ...

# 4. 等 watchdog 一次 supervise 周期（默认 30s），不要手工干预
sleep 35

# 5. 再看 ps —— 旧的 11111 应该被 SIGTERM 掉了，只剩新 router 拉起的新订阅链
ps -eo pid,ppid,command | grep '@larksuite/cli.*+subscribe' | grep -v grep
# 期望: 只有一行，PPID 是新 router 的 pid（可在 $CLAUDETEAM_STATE_DIR/router.pid 读到）
```

## Then — 校验

| 阶段 | 期望 |
| --- | --- |
| SIGKILL router 后立即 | router.pid 文件还在但已经死 / 一条 PPID=1 的孤儿订阅 |
| watchdog 下一轮 supervise | log 里多一行 `♻️  reaped 1 orphan router subprocess(es)`；旧订阅 PID 消失 |
| respawn 完成后 | 唯一的 `+subscribe` 进程 PPID 是新 router pid；router.pid 是新值 |
| `claudeteam health` | router alive，watchdog alive |

## 反例 — 没有 reap 时会发生什么（仅历史佐证，当前已修复）

旧版本 round-65 之前没有 `reap_orphans`：

- 步骤 5 会看到 **两条** `+subscribe`，一条 PPID=1（孤儿），一条 PPID=新 router
- 飞书事件随机分给两边，新 router 的 `process_lines` 大约只看到 50% 消息
- 对外现象：群里发消息，agent **偶尔**收得到 — 操作员排查会怀疑网络 / lark-cli bug
- 唯一干净的恢复是手工 `pkill -f '@larksuite/cli.*+subscribe'` + 重启 router

## 错误路径

- `ps` 命令不可用（极少见，POSIX 系统几乎都有）→ `list_orphan_pids` 静默返回 []，
  没有 reap 但也不阻塞 respawn。等于退化到"事件分裂"行为。看 watchdog 日志没有
  `♻️  reaped` 行就是这种情况。
- 孤儿进程在 SIGTERM 后没死 → 现在的实现不会升级到 SIGKILL（一次 SIGTERM 后继续）。
  实际中 npm exec 几乎都吃 SIGTERM；万一不吃，下一轮 supervise 会再扫一次再 SIGTERM。
- 跨用户 ps（pid 属于别的 uid）→ `os.kill(pid, SIGTERM)` 抛 PermissionError，被吞掉，
  不会算作已 reap。这种情况只会在多用户 host 多账号跑 claudeteam 时出现。

## Why this is here

round-65 之前老板在主机上手工 `kill -9 $(cat router.pid)` 测过守护重启 — 看上去 OK
（`claudeteam health` 全绿），实际飞书事件已经 50/50 分裂。Bug 只在群里发足够多消息
才暴露。这个 playbook 把"看起来好但其实在丢消息"的隐性 bug 显式化。

任何人改 `runtime/watchdog.py` 的 `respawn` / `reap_orphans` / orphan_markers
都应该跑一次这个剧本验证。

## Out of scope

- **跨主机 orphan**：当前只扫本机 ps，不会反应到其他 host 的 router 残留。
- **非 lark-cli 守护**：`reap_orphans` 只匹配 `('@larksuite/cli', '+subscribe')` 标记。
  未来加 daemon 子进程链时需要给那个 ProcessSpec 也加 `orphan_markers`。
