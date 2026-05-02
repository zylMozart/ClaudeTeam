# Spawn command shape per CLI adapter

## 场景
验证三种 CLI 适配器（Claude Code / OpenAI Codex / Moonshot Kimi）生成的启动命令字符串格式正确。这是后续"router → tmux pane spawn agent"的最底层契约：spawn_cmd 错了，agent 永远启不起来。

不真启进程，只验命令字符串符合每种 CLI 的二进制约定。

## 范围
- 类型：host-only
- 凭证：none

## Given
- 已 `pip install -e .` 或 `PYTHONPATH=src` 让 `claudeteam.agents` 可导入

## When

```python
from claudeteam.agents import get_adapter

cc = get_adapter("claude-code").spawn_cmd("worker_cc", "sonnet-4-6")
codex = get_adapter("codex-cli").spawn_cmd("worker_codex", "gpt-5.5")
codex_alt = get_adapter("codex-cli").spawn_cmd("worker_codex", "sonnet")  # non-OpenAI
kimi = get_adapter("kimi-code").spawn_cmd("worker_kimi", "")
```

## Then

1. **Claude Code** 命令含：
   - `IS_SANDBOX=1 claude --dangerously-skip-permissions`
   - `--model sonnet-4-6`
   - `--name worker_cc`
2. **Codex (gpt-5.5)** 命令含：
   - `CODEX_AGENT=worker_codex codex`
   - `--dangerously-bypass-approvals-and-sandbox`
   - `--model gpt-5.5`
3. **Codex (sonnet)** 命令含 `--dangerously-bypass-approvals-and-sandbox`，**不含** `--model`（非 OpenAI 模型默默剔除，让 Codex 用配置默认）
4. **Kimi** 命令含：
   - `DISABLE_UPDATE_CHECK=1 KIMI_AGENT=worker_kimi`
   - `kimi --yolo`
5. `get_adapter("kimi-cli")` 与 `get_adapter("kimi-code")` 返回同一个实例（alias）
6. `get_adapter("not-a-cli")` 抛 `KeyError("unknown cli: 'not-a-cli' (known: ...)")`

## 证据（执行时填）

```
- 命令: …
- 各适配器输出原文: …
- 结果: pass | fail
- 后续: …
```
