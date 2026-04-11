# ClaudeTeam 代码规范

> 所有 Agent 编写代码时必须遵循本规范。Manager 在代码审查时以此为准。

---

## 1. Python 文件结构

每个 `.py` 文件遵循固定结构：

```python
#!/usr/bin/env python3
"""
模块简述 — ClaudeTeam

用法（若为 CLI 脚本）:
  python3 scripts/xxx.py <参数说明>
"""
import stdlib_module
import third_party_module

sys.path.insert(0, os.path.dirname(__file__))
from config import AGENTS, TMUX_SESSION, PROJECT_ROOT  # 本地导入

# ── 章节标题 ──────────────────────────────────────────────────
```

**规则：**
- Shebang 行：`#!/usr/bin/env python3`
- 模块 docstring：第一行写"模块简述 — ClaudeTeam"，若为 CLI 脚本附带用法说明
- 导入顺序：标准库 → 第三方库 → 本项目模块，各组之间空一行
- 章节分隔符：`# ── 标题 ──` 格式，用全角破折号和半角横线对齐

---

## 2. 命名规范

| 类型 | 规范 | 示例 |
|------|------|------|
| 文件名 | `snake_case.py` | `feishu_msg.py`, `msg_queue.py` |
| 函数 | `snake_case` | `load_cfg()`, `send_message()` |
| 变量 | `snake_case` | `agent_name`, `chat_id` |
| 常量 | `UPPER_SNAKE_CASE` | `TMUX_SESSION`, `PROJECT_ROOT`, `LARK_CLI` |
| 类 | `PascalCase` | `TokenCache`（尽量少用类，本项目以函数式为主） |
| 私有/内部 | 前缀 `_` | `_load_env()`, `_cfg` |

**中文标识规则：**
- 用户可见的输出文字用中文（状态、日志、错误提示）
- 变量名、函数名、注释一律用英文
- Emoji 仅用于日志前缀，保持一致：`✅` 成功 / `❌` 失败 / `⚠️` 警告 / `📥` 收件 / `📤` 发件 / `🔄` 同步

---

## 3. 配置访问

所有配置通过 `scripts/config.py` 统一入口：

```python
from config import AGENTS, TMUX_SESSION, PROJECT_ROOT, CONFIG_FILE
from config import load_runtime_config, save_runtime_config
```

**禁止：**
- 在脚本中硬编码飞书凭据（lark-cli 统一管理认证，`lark-cli config init`）
- 直接读取 `team.json`（用 `config.py` 导出的 `AGENTS`、`TMUX_SESSION`）

**运行时配置**（`runtime_config.json`）通过 `load_runtime_config()` / `save_runtime_config()` 访问。

---

## 4. 飞书 API 调用规范

### lark-cli 调用

所有飞书 API 操作通过 `lark-cli` （`@larksuite/cli`）执行，不直接使用 `requests`：

```python
LARK_CLI = ["npx", "@larksuite/cli"]

def _lark(args, label="", timeout=30):
    r = subprocess.run(LARK_CLI + args, capture_output=True, text=True, timeout=timeout)
    if r.returncode != 0:
        print(f"   ⚠️ {label}: {r.stderr.strip()[:200]}")
        return None
    return json.loads(r.stdout) if r.stdout.strip() else {}
```

认证由 lark-cli 自动管理（`lark-cli config init` 一次性配置），无需手动获取 token。

### 常用命令

```bash
# 发群消息
lark-cli im +messages-send --chat-id oc_xxx --markdown "内容" --as bot
# Bitable 写入
lark-cli base +record-batch-create --base-token xxx --table-id xxx --json '...' --as bot
# Bitable 查询
lark-cli base +record-search --base-token xxx --table-id xxx --json '...' --as bot
```

### 错误处理

- 检查 `subprocess` 返回码 + 解析 JSON 输出
- 失败时打印错误但不轻易 `sys.exit()`（让调用者决定是否中断）

---

## 5. 进程管理规范

### PID 锁文件

长期运行的守护进程（router、watchdog、kanban_sync）必须使用 PID 锁：

```python
PID_FILE = os.path.join(os.path.dirname(__file__), ".process_name.pid")

def acquire_pid_lock():
    if os.path.exists(PID_FILE):
        with open(PID_FILE) as f:
            old_pid = int(f.read().strip())
        try:
            os.kill(old_pid, 0)  # 检查进程是否存活
            print(f"❌ 已有实例运行 (PID {old_pid})，退出")
            sys.exit(1)
        except OSError:
            pass  # 旧进程已死，可以接管
    with open(PID_FILE, "w") as f:
        f.write(str(os.getpid()))
```

进程退出时通过 `atexit` 清理 PID 文件。

### tmux 消息注入

通过 `tmux_utils.py` 的 `inject_when_idle()` 函数发送消息给 agent：
- 单次 Enter，间隔 0.5 秒，**禁止循环重试 Enter**（会导致消息重复发送）
- 发送前检查 agent 是否空闲（`is_agent_idle()`）
- 队列有积压时入队等待，保证 FIFO 顺序

---

## 6. 输出与日志

### 脚本输出格式

```python
print(f"✅ 群聊已创建: {chat_id}")          # 成功
print(f"❌ 发送失败: {error_msg}")            # 失败
print(f"⚠️  Token 即将过期，已刷新")          # 警告
print(f"  📥 消息已入队 {agent_name}")        # 子步骤（缩进2空格）
```

- 主步骤无缩进，子步骤缩进 2 空格
- Emoji + 空格 + 描述，保持对齐
- 中间过程用 `flush=True` 避免输出延迟

### 禁止

- 不要用 `logging` 模块（本项目统一用 `print`，输出直接进 tmux）
- 不要在循环中大量打印（会淹没 agent 的 tmux 窗口）

---

## 7. 错误处理原则

```
外部边界（API、文件I/O、用户输入）→ 捕获并处理
内部调用 → 信任，不加冗余 try/except
```

- lark-cli 调用：检查 `subprocess` 返回码 + JSON 解析
- 文件读写：`os.path.exists()` 预检查，或 `try/except FileNotFoundError`
- JSON 解析：`try/except (json.JSONDecodeError, KeyError)`
- 内部函数调用：不加 try/except，让异常自然冒泡

---

## 8. 脚本入口模式

CLI 脚本统一使用 `if __name__ == "__main__"` + `sys.argv` 解析：

```python
if __name__ == "__main__":
    if len(sys.argv) < 2:
        print(__doc__)
        sys.exit(1)

    cmd = sys.argv[1]
    if cmd == "send":
        # ...
    elif cmd == "inbox":
        # ...
    else:
        print(f"❌ 未知命令: {cmd}")
        sys.exit(1)
```

- 无参数时打印模块 docstring（`__doc__`）作为帮助
- 不使用 `argparse`（保持轻量，agent 通过 feishu_msg.py 调用）

---

## 9. Shell 脚本规范

```bash
#!/bin/bash
# 脚本简述
# 用法：bash scripts/xxx.sh [参数]

set -e  # 出错即停

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
PROJECT_ROOT="$(dirname "$SCRIPT_DIR")"
cd "$PROJECT_ROOT"
```

- `set -e`：所有 bash 脚本必须开启
- 路径：用 `$PROJECT_ROOT` 取绝对路径，不依赖 `pwd`
- 变量：全部双引号包裹 `"$VAR"`
- 命令替换：`$(command)` 不用反引号

---

## 10. Git 提交规范

```
<type>: <简要描述>

<可选正文：解释 what 和 why>
```

| type | 用途 |
|------|------|
| `feat` | 新功能 |
| `fix` | Bug 修复 |
| `refactor` | 重构（不改变功能） |
| `docs` | 文档 |
| `style` | 代码规范（仅涉及代码风格、格式化） |
| `test` | 测试 |
| `chore` | 构建、依赖、配置变更 |

- 标题 ≤ 70 字符
- 正文说明"为什么改"而非"改了什么"
- 每个 PR 聚焦一个主题

---

## 11. 文件组织

```
scripts/          ← 运行时基础设施（所有用户共享）
  config.py       ← 配置入口（唯一）
  feishu_msg.py   ← 消息总线（lark-cli 封装层）
  feishu_router.py← 消息路由（lark-cli WebSocket 事件流）
  msg_queue.py    ← 消息待投递队列
  watchdog.py     ← 进程监控
  ...
templates/        ← Agent 身份模板
docs/             ← 项目文档
```

**新增脚本规则：**
- 放入 `scripts/` 目录
- 通过 `config.py` 获取配置
- 飞书 API 操作通过 `lark-cli` 命令执行（`subprocess.run`）
- 若为守护进程，在 `watchdog.py` 中注册监控
- 更新 `docs/` 中的架构说明

---

## 12. 代码审查清单

提交前自查：

- [ ] 无硬编码的密钥、Token、API 地址
- [ ] 导入顺序正确（stdlib → third-party → local）
- [ ] lark-cli 调用有错误处理
- [ ] 守护进程有 PID 锁
- [ ] tmux 消息注入用 `inject_when_idle()`，无循环 Enter
- [ ] 输出格式与现有脚本一致（Emoji + 缩进）
- [ ] 不在项目根目录创建文件
- [ ] `set -e` 在 bash 脚本中
- [ ] 变量名全英文，用户可见文字中文
