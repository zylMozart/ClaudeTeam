# Version probe

## 场景

shell 脚本 / CI gate / smoke conductor 想知道 "this host 上跑的是哪
个 ClaudeTeam"。`claudeteam version` 在 stdout 打印一行 semver-ish
字符串就够 —— 轻量到能 inline 在条件判断里。

## 范围

- 类型：local-only
- 凭证：无
- 操作员：boss / CI

## Given

- ClaudeTeam 通过 `pip install -e .` 安过（pyproject.toml `version`
  字段被 setuptools 写入 distribution metadata）

## When

```bash
claudeteam version
# → 0.1.0

# 在 shell 脚本里 gate
if [ "$(claudeteam version)" != "0.1.0" ]; then
    echo "wrong version, refusing to proceed"
    exit 1
fi

# smoke conductor 提交报告时记录
SMOKE_VERSION=$(claudeteam version)
```

## Then

stdout 一行 SemVer-ish 字符串（pyproject.toml `[project] version`
当前是 `0.1.0`）。stderr 空，exit 0。

非常用情况:

- 包没装好 (没 pip install -e)：fallback `0.0.0+unknown`，仍然 exit 0
  (CLI invariant: 命令要么成功要么有可读错误，不能 crash)
- `claudeteam version --help` → "usage: claudeteam version" + exit 0

## Why this is here

跟 git rev-parse 性质类似 —— machine-readable 入口让 CI / smoke /
support tickets 不再用 `pip show claudeteam | grep Version` 这种间
接方式。CLAUDE.md 工作单"ship version" 落 0.1.0 后这是配套的
operator UX。

## Out of scope

- **build hash / commit sha**：现在只读 pyproject.toml 的 version
  字符串。要查 git sha 用 `git rev-parse HEAD`。如果 future 想塞
  build hash 进去（reproducible-build 风格），改 `_read_version` 的
  fallback 链。
- **`claudeteam --version`**: 全局 `--version` flag 没实装；要走
  subcommand `claudeteam version`。这是 sub-command 风格 CLI 的常见
  约定（kubectl / git 也一样）。
