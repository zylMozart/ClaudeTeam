# `claudeteam usage`: token / credit snapshot

## 场景
老板想知道这一天 / 这一月 团队烧了多少 token、多少 credit。`claudeteam usage` 用一条命令打完整摘要，按 CLI 维度分组：claude-code 走第三方 `ccusage` 工具（读 ~/.claude/projects 日志），其它 CLI 没上游工具就明说"去看你自己的 dashboard"。

不假设 npx 装好；npx 不存在打 `(npx not on PATH; install Node.js)`，不会卡住。

## 范围
- 类型：host-only（需要 ccusage 装好才能看到 claude 维度真实数据）
- 凭证：none

## Given
- `claudeteam` CLI 已装
- 当前 team.json 至少含一个 claude-code agent
- 可选：`npm i -g ccusage` 或者准备好 npx 能拉

## When

```bash
# 默认 daily view
claudeteam usage

# 月度
claudeteam usage --view monthly

# 不同 view
claudeteam usage --view session
claudeteam usage --view blocks
```

## Then
1. 输出按段：`━━ usage (daily) ━━` / `claude-code (via ccusage):` / `other CLIs:`
2. claude-code 段：
   - 装了 ccusage：列出每行 token 总数和成本
   - 没装：`⚠️ ccusage failed:` + ccusage 报错原文
   - 没 npx：`(npx not on PATH; install Node.js to use ccusage)`
3. other CLIs 段：`codex-cli: no upstream usage tool — track via the provider dashboard`
4. exit code 始终为 0（usage 是只读快照，不该因为 ccusage 装没装就报错）
5. `--view` 不在 {daily, monthly, session, blocks} 时 exit 1

## 反例
- `claudeteam usage --bogus` → exit 1，stderr 含 `unexpected args`
- `claudeteam usage --view foo` → exit 1，stderr 含 `unknown view`

## 证据（执行时填）

```
- T_usage: …
- ccusage 安装情况: 装了 / 没装 / npx 没装
- 各 CLI 段输出: …
- 后续: …
```
