# tests/ — 测试怎么写、怎么跑

## 跑

```bash
python3 tests/run.py            # 全量（stdlib runner，无 pytest 依赖）
python3 tests/run.py acp        # 子串过滤模块名
```

提交前必须全绿。runner 发现 `tests/{unit,integration}/test_*.py` 里
所有 `test_*` 顶层函数——不认 class，不认 fixture 装饰器。

## 写

- 公共设施只从 `helpers.py` 拿：`isolated_env()`（隔离 state dir +
  team 配置）、`run_cli()`、`env_patch` / `attr_patch` / `tmux_patch`、
  `FakeProc` / `CallRecorder`。别复制粘贴自己的 `_isolated_state`。
- **fixture 里的 runner 要显式**：团队配置写 `"runner": "tmux"` 还是
  留默认（ACP 能力 CLI → acp）决定走哪条投递路径。测 tmux 行为
  （inject/wake/capture）就钉 tmux；测 ACP 行为就别钉。2026-07 有
  30 个测试因为这个默认翻转集体挂过。
- ACP 相关测试用 `fake_acp_agent.py`（真子进程、真 JSON-RPC wire、
  行为用 FAKE_ACP_* env 编排：回声/延迟/死亡/要权限）。测 host 层用
  `test_runtime_acp_host.py` 里的 `FakeAcpAdapter` 模式。
- 轮询等待用小步 `_wait(pred, timeout)` 模式，别裸 sleep 固定秒数。

## scenarios/（人工回归剧本）

`tests/scenarios/*.md` 是操作者对真实部署跑的 Given/When/Then。
新公共命令、新用户可见行为，同 commit 配一份。自动化档位
（unit/integration）覆盖不了"真飞书 + 真 CLI"这一层，这些剧本就是
那一层的测试。
