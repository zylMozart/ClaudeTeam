---
name: tmux
description: "本地 pane 截屏拦截。零 LLM 调用。用法：/tmux [agent] [lines]"
---

# /tmux — 本地 tmux pane 截屏

通过 UserPromptSubmit hook 拦截，不走模型。
如果 hook 未生效，本 skill 作为 fallback 被调用。

## 用法
- `/tmux` — manager 窗口最后 10 行
- `/tmux devops` — devops 窗口最后 10 行
- `/tmux devops 30` — devops 窗口最后 30 行
- `/tmux security 200` — security 窗口最后 200 行（上限 2000）

## 执行
读取参数，运行：
```bash
tmux capture-pane -pt <session>:<agent> -S -<lines>
```
直接输出结果。
