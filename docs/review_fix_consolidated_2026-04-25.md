# Review · #60 Batch 1+2 fix consolidated

- 日期: 2026-04-25
- 评审人: toolsmith
- 上游 spec: `docs/fix_spec_consolidated_2026-04-25.md`（13 节 625 行）
- coder commit: `restructure/a209b56` — 9 files, 223 insertions
- onboarding sandbox（非 git）: `restructure-onboarding/` — 7 files

---

## 0. Verdict

**PASS-with-minor**

8 个 review 重点全部通过（spec §13）；§12 验收 17 项中 11 项 PASS-by-static / 5 项 NEEDS-RUNTIME（属 qa_smoke 域，本轮无法静态验证）/ 1 项 N/A。
新发现 4 个 L 级 finding（PYTHONPATH 注入兼容性 / dead code / 文档边界），均不阻塞。

可放 qa_smoke。

---

## 1. Spec §13 八大重点逐项

| # | 重点 | 结论 | 证据 |
|---|------|------|------|
| 1 | 路由前缀正则边界 | ✅ PASS | `state.py:99` 正则 `^\s*@?(<agents>)(?:\s*[:：]\s*|\s+)(.+)$` + `IGNORECASE | DOTALL`；`agents` 按长度倒排（worker_codex 优先 worker）；canon 用 `a.lower()==m.group(1).lower()` 回查；body 经 `.strip()` 干净；空 body 落 None；中文 `：` / ASCII `:` / 空格三种分隔均测试覆盖 |
| 2 | mock-boss 守卫双条件 | ✅ PASS | `state.py:33` `_boss_mock = (BOSS_MOCK=="1") and (RUNTIME_PROFILE=="smoke")`；`is_bot_message` 头部 `if self._boss_mock: return False`；prod 默认双 0 时 guard 不触发，bot 自送照常被识别 |
| 3 | Opus 4.7 注入路径 | ✅ PASS | `runtime/config.py:114-115` ALLOWED_MODELS 加入 `opus-4-7`/`claude-opus-4-7` 两种写法；team.json `manager.model="claude-opus-4-7"`；`agent_lifecycle.sh:143` resolve-model 走单事实源；`claude_code.py:7` spawn_cmd `claude --model {model}` 直传。settings.json 无 model 字段（无回退路径，但 CLI 拒绝时 fail-loud 而非默死） |
| 4 | PYTHONPATH 三道注入 | ⚠️ PASS-with-1L | Dockerfile ENV / compose env / entrypoint export 三处都到位；但 compose `${PYTHONPATH:-/app/src}` 读取 host env，host 若已设了不相关 PYTHONPATH 会被传入容器，entrypoint `${PYTHONPATH:-/app/src}` 不再 fallback（非空就保持）。见 finding F-N1 |
| 5 | LAZY_AGENTS 白名单默认值 manager 不在内 | ✅ PASS | `tmux_team_bringup.sh:30` 默认 `worker_cc,worker_codex,worker_kimi,worker_gemini`；compose 同步默认；manager 永不 lazy；`should_skip_agent_in_lazy_mode` 反向判定（`is_lazy_eligible` 为真才跳过 spawn），逻辑对位 |
| 6 | bitable 撤回边界 | ✅ PASS | `grep bitable router/*.py feishu_router.py` 0 hits；router 整层无 bitable 调用；spec §5.2 撤回边界守住 |
| 7 | 命名收回（onboarding 副本） | ✅ PASS | `onboarding/team.json` 已改 manager + worker_cc/codex/kimi/gemini；docs/onboarding 5 文件 0 处 lead/agent_a-d 命中；与 restructure/team.json 命名空间一致 |
| 8 | lark-cli 命令矫正 | ✅ PASS | `create_group.sh:62-64` 已替换为 `im chats link`，注释明确说明 v1.0.19 不存在 `+get-chat-link` |

---

## 2. Spec §12 验收 checklist 17 项

| # | 项 | 判定 | 一句话证据 |
|---|----|------|-----------|
| 1 | team.json 全用 manager + worker_* | ✅ PASS | restructure & onboarding 两份均重命名 |
| 2 | manager pane 看到 claude UI（非 💤） | ✅ PASS | 不在 LAZY_AGENTS 默认 → eager spawn |
| 3 | worker_* pane 仍 💤 | ✅ PASS | LAZY_AGENTS 默认 = 4 worker；lazy_mode=on 时跳 spawn |
| 4 | router 前缀路由 worker_cc 你好 → worker_cc | ✅ PASS | `dispatch.py:99-104` 优先级位于 manager fallback 之前；prefix_route 标 result.reason |
| 5 | 无前缀 → 默认 manager 兜底 | ✅ PASS | `dispatch.py:106` 兜底 `["manager"]` 路径未变 |
| 6 | mock-boss bot 自发当 boss-message | ✅ PASS | `state.py:_boss_mock` 双 guard + `is_bot_message` 短路 |
| 7 | .env.smoke 存在，cp 后启动成功 | ✅ PASS（存在）/ ⏭️ NEEDS-RUNTIME（启动） | 文件验证；启动属 qa_smoke |
| 8 | which claude 返回路径 | ✅ PASS-by-static | Dockerfile ARG 默认 1 + npm install -g @anthropic-ai/claude-code |
| 9 | echo $PYTHONPATH = /app/src | ✅ PASS-by-static | 三道注入；F-N1 仅在 host 显式 set 时降级 |
| 10 | import claudeteam 不报错 | ✅ PASS-by-static | PYTHONPATH=/app/src + src/claudeteam 包结构完好 |
| 11 | /usage 显示 4 项 | ⏭️ N/A | 本 commit 无 /usage 改动；属 prior 改动域 |
| 12 | /send worker_cc hello → 先 wake 再收 | ⏭️ NEEDS-RUNTIME | wake_on_deliver 走 lazy_wake_v2，链路通；端到端要 qa_smoke |
| 13 | G1 全员报道 5 条 say | ⏭️ NEEDS-RUNTIME | manager broadcast 责任在 prompt 层（spec §2.3 F-G1-NEW），不在本 commit 代码改 |
| 14 | G8 prefix-route 唤醒 worker_cc | ⏭️ NEEDS-RUNTIME | dispatch + wake_on_deliver 链路就位；端到端要 qa_smoke |
| 15 | bitable 不被写入 | ✅ PASS-by-static | router 0 bitable 调用 |
| 16 | manager pane 跑 Opus 4.7 | ✅ PASS-by-static | team.json + ALLOWED_MODELS + spawn_cmd `--model` 三段对齐 |
| 17 | qa_smoke 30 min 全程 | ⏭️ NEEDS-RUNTIME | 端到端时间预算属 qa_smoke 验收 |

合计：**11 PASS-by-static + 5 NEEDS-RUNTIME + 1 N/A**。

---

## 3. 新发现 finding（不在原 spec finding 列表）

| ID | 等级 | 描述 | 建议 |
|----|------|------|------|
| F-N1 | L | compose 用 `PYTHONPATH=${PYTHONPATH:-/app/src}` 读取 host env；host 若已 export PYTHONPATH，会传入容器并覆盖 Dockerfile ENV，entrypoint `${PYTHONPATH:-/app/src}` 仅在空时 fallback，非空直接保留→可能丢失 /app/src | compose 改 `PYTHONPATH=/app/src${PYTHONPATH:+:$PYTHONPATH}`（永远 prepend），或 entrypoint 主动 prepend 而非纯 fallback |
| F-N2 | L | `tmux_team_bringup.sh:42` 保留 `is_lazy_whitelist` 兼容 shim 但 `grep -R is_lazy_whitelist scripts/` 0 调用（旧 caller 已全改 `should_skip_agent_in_lazy_mode`） | 下一轮可清掉；本轮不动也无害 |
| F-N3 | L | settings.json 模板（Dockerfile RUN 那段）无 `"model"` 字段→spec §13.3 提的"settings.json 回退"实际无回退路径。若 CLI 拒绝 `--model claude-opus-4-7`，spawn 直接 fail-loud（不会无声降级到 sonnet） | 当前 fail-loud 是更安全的语义；只是 spec 表述需对齐——非代码 bug |
| F-N4 | info | Dockerfile `ENV PYTHONPATH=/app/src` 在 compose `environment` 覆盖下实际不生效；仅在 `docker run` 不带 -e 时起作用，是个有用的兜底 | 不动；记录一下"三道注入"中 Dockerfile ENV 的实际语义是 compose-旁路 fallback |

---

## 4. 路由前缀正则边界细测

跑了 5 个新增 dispatch case + 既有 15 个 → **20/20 PASS**。

补测想象中的边界（口算，未额外写 case）：

| 输入 | 预期 | 推断 |
|------|------|------|
| `worker_cc:` （空 body） | None / 落 manager 兜底 | ✅ `body.strip()=="" → return None,text` |
| `WORKER_CC: hi` | worker_cc / "hi" | ✅ IGNORECASE + canon 回查 |
| `worker_xx hi` | None / 落 manager | ✅ `next(canon)=None`（已有 case） |
| `worker_codex 任务` （worker 子串干扰） | worker_codex / "任务" | ✅ longest-first 排序 |
| `worker_cc:任务\n第二行` | worker_cc / "任务\n第二行" | ✅ DOTALL 跨行 |
| `worker_cc，hi` （中文逗号） | None / 落 manager | ✅ 分隔符限定 `:`/`：`/ 空白；逗号不匹配 |
| `  @worker_cc  hi` （多空白） | worker_cc / "hi" | ✅ `^\s*@?` + `\s*[:：]\s*\|\s+` 吃空白 |

---

## 5. mock-boss 守卫边界

| BOSS_MOCK | RUNTIME_PROFILE | _boss_mock | 行为 |
|-----------|-----------------|-----------|------|
| 1 | smoke | True | bot 自发被当 boss msg（mock 路径） |
| 1 | （未设/prod） | False | bot 自发照常识别为 bot self |
| 0 | smoke | False | 同上 |
| （未设） | smoke | False | 同上 |
| 1 | "smoke " (含尾空格) | False | 严格相等比较，不容忍空格——✅ 安全 |

✅ 双 guard 实现严格，prod 不会误开。

---

## 6. LAZY_AGENTS 默认值复检

| 配置 | manager spawn | worker_cc spawn | 期望 |
|------|--------------|-----------------|------|
| LAZY_MODE=on, LAZY_AGENTS 未设 | eager | 跳（lazy） | ✅ |
| LAZY_MODE=on, LAZY_AGENTS="" | eager | eager | ✅（显式空 = 全员 eager） |
| LAZY_MODE=on, LAZY_AGENTS="manager,worker_cc" | 跳（lazy） ⚠️ | 跳（lazy） | ⚠️ 用户显式让 manager lazy 时，spec §2.1 "manager 永不 lazy" 不再守住 |
| LAZY_MODE=off | eager | eager | ✅ |

第 3 行虽然合规（用户显式覆盖），但与 spec §2.1 "manager always-eager" 不完全对齐。当前实现遵循"显式覆盖优先"，工程惯例合理；如需硬保护建议在 `is_lazy_eligible` 加 `[[ "$agent" == "manager" ]] && return 1` 短路。**非阻塞，记 info**。

---

## 7. 边界守住

- restructure 仓 router/feishu_router 整层 0 bitable 调用 ✅
- onboarding 副本未触碰原仓 git 状态 ✅
- `git -C restructure log -1 --stat a209b56` 9 files 全在仓内允许域，无误改 prod-hardened.yml / live-smoke override ✅

---

## 8. 评审用到的命令

```bash
git -C restructure show --stat a209b56
git -C restructure show a209b56 -- src/claudeteam/messaging/router/state.py
git -C restructure show a209b56 -- src/claudeteam/messaging/router/dispatch.py
git -C restructure show a209b56 -- src/claudeteam/runtime/config.py
git -C restructure show a209b56 -- Dockerfile docker-compose.yml
git -C restructure show a209b56 -- scripts/docker-entrypoint.sh
git -C restructure show a209b56 -- scripts/lib/tmux_team_bringup.sh
git -C restructure show a209b56 -- tests/test_router_dispatch.py

cd restructure && python3 tests/test_router_dispatch.py   # 20/20 PASS
grep -n bitable src/claudeteam/messaging/router/*.py scripts/feishu_router.py
grep -n PYTHONPATH Dockerfile docker-compose.yml scripts/docker-entrypoint.sh
grep -n CLAUDETEAM_LAZY_AGENTS scripts/lib/tmux_team_bringup.sh docker-compose.yml
grep -REwn 'lead|agent_a|agent_b|agent_c|agent_d' onboarding/docs/onboarding/ onboarding/team.json
cat onboarding/scripts/onboarding/say_as_boss.sh
cat onboarding/.env.smoke
```
