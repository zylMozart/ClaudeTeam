# `claudeteam init`: first-time bootstrap

## 场景
新用户拿到这个仓库，想本地起一个 4 人团队。`claudeteam init` 一次写出 `team.json`（含 manager + 3 worker 的默认配置）和 `runtime_config.json`（chat_id 留空待填），然后能直接 `claudeteam start` 起 tmux session。

不依赖飞书；不联网。是部署链路的第一步。

## 范围
- 类型：host-only
- 凭证：none

## Given
- `claudeteam` CLI 已安装（`pip install -e .`）
- 当前目录干净：没有 `team.json` 也没有 `runtime_config.json`

## When

```bash
# 默认 session 名 ClaudeTeam
claudeteam init

# 或者起个自定义 session 名
claudeteam init --session AlphaTeam --force   # --force 会覆写已有文件
```

## Then

1. **`claudeteam init`** 退出 0；stdout 含两行 `wrote ...` + 一段 "Next:" 提示
2. `team.json` 包含 `manager / worker_cc / worker_codex / worker_kimi` 四个 agent，每人 `cli` + `model` + `role` 都填好
3. `runtime_config.json` 包含 `{"chat_id": "", "lark_profile": ""}` —— 待人工填
4. 重跑 `claudeteam init` 退出 1，stderr 含 `team.json already exists`
5. 加 `--force` 重跑退出 0，**会覆盖**两个文件
6. `--session AlphaTeam --force` 后 `team.json` 中 `session == "AlphaTeam"`
7. 立刻 `claudeteam start` 能起来（验证生成的 team.json 是合法的）

## 反例
- `claudeteam init --bogus` → 退出 1，stderr 含 `unexpected args`

## 证据（执行时填）

```
- T_init: …
- 文件路径: team.json / runtime_config.json
- 重跑被拒结果: …
- --force 覆写结果: …
- claudeteam start 是否成功: pass | fail
- 后续: …
```
