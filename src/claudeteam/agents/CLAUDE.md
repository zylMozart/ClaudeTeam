# agents/ — CLI 适配器（每个受支持的 agent CLI 一个文件）

## 职责边界

一个 `CliAdapter` 只回答"这个 CLI 怎么用"的**数据问题**：
怎么拼 spawn 命令、什么字样代表启动完成、进程名叫什么、凭据放哪、
提交键是什么、有没有 ACP 适配器。**流程**（怎么起 pane、怎么解析凭据
优先级、怎么注入）都在 runtime/ —— 适配器里不写流程。

## 加一个新 CLI（照根目录 recipe，要点重复一遍）

1. 抄一个最像的现有文件（OpenAI 兼容的抄 `trae_cli.py`，独立生态的抄
   `gemini_cli.py`）。
2. `agents/__init__.py` 的 `_REGISTRY` 注册（上游包名不同就加 alias）。
3. `docs/DEPLOYMENT.md` + `DEPLOYMENT_zh.md` 的安装表格各加一行。
4. `tests/unit/test_agents_<名>.py`。

## 铁律（违反过一次就会出安全/供应商锁定事故）

- **绝不硬编码** endpoint、API key、供应商名、默认模型。endpoint 读
  `$OPENAI_BASE_URL`，key 走 `auth_slots()` → `runtime/agent_auth`
  的 token > login > api_key 解析。model 用调用方传进来的值。
- 凭据永远不进 spawn 命令明文（会留在 pane 回滚缓冲和 agent 上下文里），
  由 `lifecycle.build_spawn_command` 写 0600 文件 source 进去。

## ACP 支持

- CLI 有 ACP 适配器（如 zed 的 claude-code-acp / codex-acp）→ 覆写
  `acp_argv()`（返回 argv）和 `acp_env()`（HOME 隔离、model 选择等
  env dict，等价于 spawn_cmd 里的 env 前缀但不用 shell 转义）。
- 覆写了 `acp_argv` 的 CLI，agent 默认走 acp runner —— `submit_keys` /
  `ready_markers` / `resubmit_on_idle` 这些 TUI 补偿逻辑对它不再生效
  （但保留：操作者可以钉 `runner = "tmux"` 回退）。
