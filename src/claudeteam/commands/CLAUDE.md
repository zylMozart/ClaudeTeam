# commands/ — 一个文件 = 一个 `claudeteam` 子命令

在这个目录写代码前先读根目录 CLAUDE.md 的 "Maintenance recipes"。

## 这里放什么 / 不放什么

- 每个文件就是一个薄薄的 argv 适配器：解析参数 → 调 runtime/store 的
  函数 → print 结果 → return int。目标 ~30 行，超过 150 行说明业务
  逻辑放错了地方（挪去 runtime/ 或 store/）。
- 参照物是 `health.py`（`_check_*` helpers + 报告累加器 + main）。
  新命令长得和它差异很大时，在模块 docstring 里写一行为什么。

## 固定模式（照抄，别发明新的）

```python
USAGE = "usage: claudeteam foo <agent> [--bar]"

def main(argv: list[str]) -> int:
    if maybe_print_help(argv, USAGE):
        return 0
    if len(argv) < 1:
        return usage_error(USAGE)
    ...
    return 0        # 非零 = 失败；error_exit() 打印并返回 1
```

- 注册：`cli.py` 的 COMMANDS 表加一行，否则命令不存在。
- 同一个 commit 里必须有 `tests/unit/test_commands_<名>.py` 和
  `tests/scenarios/<名>.md`（操作者手动回归剧本）。

## 常见坑

- **runner 分岔**：任何"戳 agent 的 pane"的操作（inject/capture/送键）
  都要先看 `config.agent_runner(agent)` —— acp agent 的 pane 只是
  transcript viewer，真身在 router 的 AcpHost 里。给 acp agent 递话用
  `store/acp_queue.enqueue`，看输出读 `acp/<agent>/transcript.log`。
  例子：`send.py` / `peek.py` / `restart.py` 里都有现成分支可抄。
- **retired 门**：动 pane / 队列前先 `local_facts.is_retired(agent)`，
  被 fire 的 agent 绝不能被顺手复活（deliberate 复活只走 `hire`）。
- 副作用函数一律留 `run=` / `popen=` 之类的可注入参数，测试才写得动。
