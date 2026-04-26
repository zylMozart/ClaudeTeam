# Review · creds persistence v2 实施

- 日期: 2026-04-25
- 评审人: toolsmith
- 上游 spec: `docs/architect_creds_persistence_v2_2026-04-25.md`（architect_v2，方案 A）
- coder 改动范围: `docker-compose.live-smoke.override.yml` 单文件，14 行注释 + `services: {}`

---

## 0. Verdict

**PASS-with-minor**

- 净效果正确：live-smoke 完全继承 dev compose 的凭证挂载（root + 直挂 host `~/.claude/.credentials.json` + `~/.claude.json`），host `/login` 后秒级同步。
- 边界守住：`prod-hardened.yml` / `docker-compose.yml` / `scripts/*` 本轮无改动。
- 2 个 minor finding，均不阻塞 qa_smoke（一个是环境前置缺 `.env`，一个是 spec §5.1 #2 文档章节漏交）。
- 建议：F2（M）顺手补一下 TROUBLESHOOTING.md 那节再放 qa_smoke；F1（L）不阻塞，记一下知识。

---

## 1. 改动事实核对

### 1.1 git diff 摘要

```
docker-compose.live-smoke.override.yml | 21 ++++++++++++---------
1 file changed, 12 insertions(+), 9 deletions(-)
```

旧文件（6 行实质 yaml）:

```yaml
services:
  team-prod-hardened:
    user: "999:999"
    volumes:
      - ./.claude-credentials/.claude.json:/home/claudeteam/.claude.json
      - ./.claude-credentials/.claude/.credentials.json:/home/claudeteam/.claude/.credentials.json
```

新文件（实质 1 行 yaml + 14 行注释）:

```yaml
# live-smoke override · 2026-04-25 v2
# 历史：曾把 user 改 999:999 + 用 ./.claude-credentials/.claude.json 替换 host 直挂 …
# 现状：完全继承 dev compose 凭证挂载（user: 0:0 + 直挂）
# 文件保留为空 services 占位，是因为 docs/live_container_smoke.md:59 等仍用 `-f override` 调用 …
services: {}
```

### 1.2 净效果（与 dev compose 对照）

`docker-compose.yml`（baseline，未改）:

- 第 44 行 `user: "0:0"` ✅
- 第 61 行 `HOME=/home/claudeteam` ✅
- 第 98 行 `~/.claude/.credentials.json:/home/claudeteam/.claude/.credentials.json` ✅
- 第 99 行 `~/.claude.json:/home/claudeteam/.claude.json` ✅

override 是空 dict → live-smoke profile 完全继承 dev → 所有原本"靠 override 改"的项都回退到 dev 默认 → host `/login` 经 rename(2) 直通容器（行为已被 spec §1.2 灰度脚本证实）。

### 1.3 spec §5.1 vs 实际交付

| # | 文件 | spec 要求 | 实际交付 | 结论 |
|---|------|-----------|----------|------|
| 1 | `docker-compose.live-smoke.override.yml` | 修法 A：清空到 `services:` 占位 | `services: {}` + 14 行注释，文件保留 | ✅ 全等价（修法 A.1 变体：保文件以兼容 docs/live_container_smoke.md:59 引用，spec §6 风险表第 5 行预案就是这个） |
| 2 | `docs/TROUBLESHOOTING.md` | 在 lazy-wake P0 后追加一节"dev/live-smoke 凭证同步原理" | TROUBLESHOOTING.md 当前只有 §7 / §8（lazy-wake P0+P1，前一轮 review 已通过），无新章节 | ❌ 未交付（F2） |
| 3 | `docker-compose.yml` 注释 | 仅当方案 A 删文件时需要 | 文件保留，所以不需要 | ✅ N/A，spec 已写"如修法 A 后没文件了"才触发 |

---

## 2. 边界核对

`git status -s` 抽取与 creds v2 相关文件：

| 文件 | 状态 | 期望 | 备注 |
|------|------|------|------|
| `docker-compose.live-smoke.override.yml` | M | 改 | ✅ 唯一允许的改动 |
| `docker-compose.prod-hardened.yml` | 未列 | 不动 | ✅ spec §5.2 红线守住（待 down，本轮不许碰） |
| `docker-compose.yml` | 未列 | 不动 | ✅ baseline 不动 |
| `scripts/claude_token_guard.sh` / `preflight_claude_auth.sh` / `docker-entrypoint.sh` | 未列 | 不动 | ✅ lazy-wake 已落盘可复用 |
| `lib/creds_sync.sh` | 不存在 | 不创建 | ✅ v1 需求已作废 |
| ClaudeTeam（host 侧） | N/A | 不动 | ✅ 本仓未涉及 |

`docs/TROUBLESHOOTING.md` 状态：M（diff 全是前一轮 lazy-wake 的 §7 §8 内容，本轮预期新增的"凭证同步原理"章节缺失，详见 F2）。

---

## 3. 风险表（spec §6）逐条复核

| 风险 | spec 评级 | 复核结论 |
|------|-----------|----------|
| 修法 A 后 live-smoke 失去 `.claude-credentials/` 隔离 | 低 | ✅ 老板已明文接受（restructure 是验证场，host 凭证就是要复用） |
| host /login 行为依赖 docker mount 的 rename 跟随 | 低 | ✅ Linux bind-mount 单文件 + rename(2) 是成熟语义；qa_smoke §4 会触发 device flow 实测 |
| 容器 root 拥有 host 凭证读权限 | 低 | ✅ dev compose 行 40-43 注释已显式接受 trade-off，本轮没动 |
| 老板将来恢复 prod-hardened | 低 | ✅ v1 spec 全文还在，本轮没删历史；override 文件作为占位也保留 |
| override 文件被外部引用 | 中 | ✅ 已 grep 全仓：唯一非自身/非 spec 引用是 `docs/live_container_smoke.md:59`，coder 选保留文件即兼顾，正是 spec §6 行 5 防御 |

无新增风险。

---

## 4. Findings

### F1 — `compose config --quiet` 因缺 `.env` 未能复验（L）

**现象**: 评审人按 spec §5.3 验收口径跑

```
docker compose -f docker-compose.yml -f docker-compose.live-smoke.override.yml config --quiet
```

报错 exit=1：

```
env file /home/admin/projects/restructure/.env not found:
stat /home/admin/projects/restructure/.env: no such file or directory
```

**根因**: dev compose 声明 `env_file: - .env`，restructure 仓内无 `.env`（环境前置缺失，与 coder 改动无关）。

**影响**: 评审无法独立复验 coder 自报的"compose config --quiet 通过"。但 yaml 语法本身正确（`services: {}` 是合法空 dict，注释合法），与 dev 合并后的最终 doc 在语义上等价于"删除 override 不带它"，不会因 yaml 结构出错。

**建议**: 把"先 `cp .env.example .env`（或类似）"写进 spec §5.3 验收口径前置，或在 README 标注。本仓后续 qa_smoke 跑前必须先解决 `.env` 缺失（不然容器都起不来），与 v2 实施无关，记到 backlog 即可。

### F2 — spec §5.1 #2 TROUBLESHOOTING.md 章节未交付（M）

**现象**: spec §5.1 #2 明确要求

> 在 lazy-wake P0 之后追加一节："dev/live-smoke 凭证同步原理"

实际 `docs/TROUBLESHOOTING.md` 当前 diff 仅包含 §7（Claude OAuth 401）+ §8（supervisor 没产出），都是上一轮 lazy-wake P0+P1 的产物（已 review 通过），无新增"凭证同步原理"章节。

**根因推测**: coder 注意力集中在 yaml 改动，文档章节漏交。

**影响**:
- 运行行为不受影响（yaml 是唯一会影响 runtime 的）。
- 但后续团队遇到 host /login 同步疑问时，TROUBLESHOOTING 没有该专节，必须翻 architect spec 才能查到原理；spec 寿命短（按需回滚），TROUBLESHOOTING 是常驻文档，缺这节会让运维侧理解断层。

**建议**:
1. 让 coder 补一节进 TROUBLESHOOTING.md，约 15-25 行即可，关键内容：
   - 为何 `~/.claude/.credentials.json` 单文件 bind-mount + `claude /login` 的 rename(2) 能让 host /login 秒级同步到容器
   - 为何 live-smoke 不再需要 `./.claude-credentials/` 隔离 + `user: 999:999` override
   - 一句话指引：override 文件保留是因为 docs 引用，里面没东西就对了
2. 或与 architect 确认是否允许延后到 v2 后续轮次（此时把 finding 改 L）。

不阻塞 qa_smoke：F2 是文档完整性问题，runtime 行为完全正确。

---

## 5. 验收口径预估（给 qa_smoke 参考）

按 spec §4 五步：

| # | 动作 | 预期 | 评审预判 |
|---|------|------|----------|
| 1 | host 跑 `claude /login` 完成 device flow | `~/.claude/.credentials.json` 刷新 | 与 v2 改动无关，host 行为 |
| 2 | `docker compose -f … -f override.yml up -d` | 容器起来；preflight exit 0；guard 在；manager 正常 | yaml 改动等价于"无 override"，预期等同 dev compose 单独启动；F1 卡点：先解决 `.env` |
| 3 | 容器内 `cat /home/claudeteam/.claude/.credentials.json` 与 host 比对 | 同步 | bind-mount 直挂，必同 |
| 4 | host 再 `/login` 一次（device flow），观测容器内秒级感知 | 容器内 hash 跟着变 | rename(2) 跨 bind-mount 行为成熟，必过 |
| 5 | 容器内手发一条简单 prompt，无 401 | OK | 凭证同步上来后 token 有效 |

预期 5 步全绿（前提是 `.env` 解决 + F2 不阻塞）。

---

## 6. 结论与下一步

- yaml 改动: PASS（全 spec 等价，含 §6 风险预案）。
- 边界: PASS（prod-hardened/dev/scripts 全没碰）。
- 文档: PARTIAL（F2 章节漏交）。
- 总评: **PASS-with-minor**，可放 qa_smoke。
- 强烈建议先补 F2 再 qa_smoke（5 分钟级动作）。
- F1 是环境问题，不归 coder，写到 backlog。

---

附：本轮评审用到的命令

```bash
git diff docker-compose.live-smoke.override.yml
git diff docs/TROUBLESHOOTING.md
git status -s | grep compose
ls docker-compose*.yml
grep -rn 'live-smoke.override' --include='*.sh' --include='*.md' --include='*.yml' --include='*.py'
docker compose -f docker-compose.yml -f docker-compose.live-smoke.override.yml config --quiet  # exit=1 .env 缺失
```
