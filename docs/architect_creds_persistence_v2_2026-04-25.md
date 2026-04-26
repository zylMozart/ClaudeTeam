# 凭证持久化方案 v2 · 简化版（dev + live-smoke）· 2026-04-25

**作者**：architect
**触发**：老板拍 C — restructure 是验证场，prod-hardened 容器要清。v1 spec 前提（prod-hardened = 产线）作废，v1 文件保留作历史参考。
**新前提**：
- 容器仅跑 `docker-compose.yml`（dev）和 `docker-compose.live-smoke.override.yml`
- 这俩 compose 本来就 bind-mount `~/.claude` → 容器，host `/login` 后**理论上**容器秒看到
- prod-hardened compose 不动（容器要 down）
**ETA**：30 分钟出稿。

---

## 0. TL;DR

**主结论**：dev compose 已经做对了 — 直挂 `~/.claude/.credentials.json` + `~/.claude.json` + `user: 0:0` + `HOME=/home/claudeteam`，host `/login` 后容器**立即生效**，**不需要写新代码**。

**唯一漏洞**：`docker-compose.live-smoke.override.yml` 把 `user` 改成 `999:999`，且把 `.claude.json` 挂载替换成本地目录 `./.claude-credentials/.claude.json`。这两个改动**联合**让 live-smoke 模式失去 host 同步能力，必须修。

**本轮交付**：
1. 验证 dev compose 现状（§1） — 无需改动，**直接可用**
2. 修 live-smoke override（§2） — 二选一，最小动作 = **删除 2 行 override**
3. preflight + token_guard 沿用 lazy-wake 修复轮交付的件，**不重写**
4. qa_smoke 5 步真机验收（§4）

**整体改动量**：1 个 compose override 文件改 ≤4 行 + 1 段 TROUBLESHOOTING 文档。无新脚本、无新挂载、无 prod-hardened 改动。

---

## 1. 验证假设：dev compose 的现有 bind-mount 真的等价 host 实时吗？

### 1.1 现状逐行验证

`docker-compose.yml` 关键 4 项：

| 行号           | 内容                                                                              | 含义                                                              |
|----------------|-----------------------------------------------------------------------------------|-------------------------------------------------------------------|
| `:44`          | `user: "0:0"`                                                                     | 容器以 root 跑                                                    |
| `:61`          | `HOME=/home/claudeteam`                                                           | claude/lark-cli 读写都走 `/home/claudeteam/.claude/...`           |
| `:98`          | `~/.claude/.credentials.json:/home/claudeteam/.claude/.credentials.json`           | host OAuth token 直挂（**单文件 bind**，host /login atomic rename 后 inode 变化时容器仍跟随）|
| `:99`          | `~/.claude.json:/home/claudeteam/.claude.json`                                    | host onboarding state 直挂                                         |

**为什么这套是对的**：

- `user: "0:0"` 让容器 root 能读 host mode 600 的文件 — uid 不匹配也无所谓（root 总能读）
- `HOME=/home/claudeteam` 把 claude CLI 的查找路径锁在 bind-mount 这一侧，避免它 fallback 到 `/root/.claude/*`
- 单文件 bind-mount（不是目录 bind-mount）的语义：host 端 inotify-aware 程序如 `claude /login` 在写 `.credentials.json` 时通常先写 `.tmp` → `rename(2)` 替换。**rename 之后旧 inode 被替换，容器侧的 bind-mount 跟随新 inode 同步**（Linux mount namespace 对 rename 透明）— 这是 docker-compose.yml 行 96 注释里"刷写 OAuth token"已经依赖的行为，本次只是再确认一遍。

### 1.2 灰度验证脚本（qa_smoke 跑 30 秒搞定）

```bash
# host 终端 1
ls -la --time=ctime ~/.claude/.credentials.json   # 记 ctime A
docker exec <container> stat /home/claudeteam/.claude/.credentials.json   # ctime A 必须等于 A

# host 终端 2
claude /login   # 走 device flow

# host 终端 1，刷新后
ls -la --time=ctime ~/.claude/.credentials.json   # 记 ctime B（应 > A）
docker exec <container> stat /home/claudeteam/.claude/.credentials.json   # ctime B 必须等于 B（不再等 A）
docker exec <container> bash scripts/preflight_claude_auth.sh   # 退 0
```

ctime 一致性 = bind-mount 工作正常。本验证不过 → §3 备选路径。

### 1.3 假设确认（结论）

**dev compose（不带 override）= 自动跟 host 同步，零代码**。
本来 v1 spec 担心的"6 小时后必死"问题，在 dev compose 上不存在 — 上一轮 lazy-wake 报告里描述的 6 小时 401，是 prod-hardened 容器的事，跟 dev 无关。

---

## 2. 唯一需要修的：live-smoke override

### 2.1 漏洞

`docker-compose.live-smoke.override.yml` 4 行：

```yaml
services:
  team-prod-hardened:           # ← 注意：service 名是 team-prod-hardened（沿用 prod-hardened service）
    user: "999:999"
    volumes:
      - ./.lark-cli-credentials/local-share:/home/claudeteam/.local/share/lark-cli:ro
      - ./.claude-credentials/.claude.json:/home/claudeteam/.claude.json:rw
```

两个问题：

1. **service 名指向 `team-prod-hardened`**：override 实际作用于 prod-hardened service，不是 dev 的 `team` service。但老板说 prod-hardened 容器要清，那么这个 override 文件的存在意义本身就要重审 — 要么改作用到 `team` service，要么删除。
2. **即使作用到正确 service**，里面：
   - `user: "999:999"` 让容器以 claudeteam(999) 跑 → host mode 600 文件读不到（uid 不匹配）
   - `./.claude-credentials/.claude.json:/home/claudeteam/.claude.json:rw` **替换**了 dev compose 的 `~/.claude.json` 挂载 → 走本地 `./.claude-credentials/`，host /login 不再同步
   - 没挂 `.credentials.json` → token 文件继承 dev 的 host 直挂；但 onboarding state 走本地，**会和 token 不一致**（同一账号但 onboarding 标记可能错位，触发 "Select login method" 菜单）

### 2.2 修法（二选一）

#### 修法 A · 删除 override（推荐，最小动作）

如果 live-smoke 的目标只是 `CLAUDETEAM_ENABLE_FEISHU_REMOTE=1` 等环境差异，把这 2 行 volumes override 删掉，user 也删掉，让 live-smoke 完全继承 dev compose 的凭证挂载。

最终 `docker-compose.live-smoke.override.yml` 仅保留 service 名 + 必要的 env override（如果有）。当前文件除凭证 override 外没别的内容（已读全文），等于**删掉就够了**。

#### 修法 B · 修对 service 名 + 修对挂载

如果一定要保留 override：

```yaml
services:
  team:                         # ← 改对 service 名
    # user: 不要 override，继承 dev 的 0:0
    volumes:
      - ~/.claude/.credentials.json:/home/claudeteam/.claude/.credentials.json
      - ~/.claude.json:/home/claudeteam/.claude.json
      # 仅在确实需要时再挂 lark-cli
      - ./.lark-cli-credentials/local-share:/home/claudeteam/.local/share/lark-cli:ro
```

**B 不是真"override"**（重复了 dev 的内容），所以**推荐 A**。

### 2.3 老板叫 down 掉 prod-hardened 容器后，prod-hardened service 留在 compose 里吗？

不在本 spec 范围（manager 处理）。本 spec 只承诺：**修法 A 实施后，dev + live-smoke 两条路径上 host /login 都能秒级同步到容器**。

---

## 3. 备选 / 兜底（仅在 §1.2 验证不过时启用）

如果灰度发现 host /login 后 docker exec stat 的 ctime **没**跟随更新（极少见，一般是 docker for mac/windows 的 inotify 限制；linux 上几乎不会），走以下兜底：

| 兜底层 | 触发                              | 动作                                                                                  |
|--------|-----------------------------------|---------------------------------------------------------------------------------------|
| L1     | preflight 容器内 exit 3           | token_guard 已落盘的 30min 循环检测 expired → manager inbox 告警（沿用 lazy-wake P0） |
| L2     | manager 收到告警                   | host 跑 `docker exec <container> kill -HUP 1` 强制 entrypoint 重启 / 或 `docker compose restart team`  |
| L3     | 上述都没用                         | 切 `ANTHROPIC_API_KEY=...` 走 API key 模式（lazy-wake P0 已实现自动短路）            |

**本轮不写新代码**：lazy-wake 修复轮已交付的 token_guard / preflight 完全够用作为兜底。

---

## 4. qa_smoke 真机验收（5 步）

灰度无新代码（修法 A 仅修一个 yaml 文件），所以验收以**真机为主**：

| # | 步骤                                                                                  | 期望                                                                                |
|---|---------------------------------------------------------------------------------------|-------------------------------------------------------------------------------------|
| 1 | host 跑 `claude /login` 走 device flow                                                | host 侧 ok；`~/.claude/.credentials.json` ctime 更新                                |
| 2 | `docker compose up -d`（dev compose 起容器，使用修过的 live-smoke override）          | 容器起来；preflight exit 0；guard 进程在；manager 窗口正常                          |
| 3 | `docker exec <container> stat /home/claudeteam/.claude/.credentials.json`             | ctime ≈ host 侧（差 < 5 秒）                                                        |
| 4 | tmux send-keys 给 worker_cc 一句"hello"                                               | 不再 401；正常回复（依赖 token_guard 已起 + lazy-wake supervisor 已恢复决策）        |
| 5 | host 重新 `claude /login` 一次模拟 token 过期续期                                     | ≤ 60 秒内容器侧 stat ctime 跟齐；之后 worker_cc 调 API 仍正常                       |

任一步骤红 → 走 §3 兜底排查。

灰度时长：1 轮通过 = pass。**不再要求 v1 里的"观察 6 小时"**：dev compose 凭证 = host 凭证，host 不到期容器就不到期。

---

## 5. 给 coder 的清单（极简）

### 5.1 必改

| # | 文件                                              | 动作                                                            |
|---|---------------------------------------------------|-----------------------------------------------------------------|
| 1 | `docker-compose.live-smoke.override.yml`          | 修法 A：清空文件成只剩 `services:` 占位 / 或干脆删除文件         |
| 2 | `docs/TROUBLESHOOTING.md`                         | 在 lazy-wake P0 之后追加一节："dev/live-smoke 凭证同步原理"     |
| 3 | （如修法 A 后没文件了）`docker-compose.yml`        | 注释里说明 live-smoke 走默认凭证挂载，无 override                |

### 5.2 不改

- ❌ 不动 `docker-compose.prod-hardened.yml`（待 down）
- ❌ 不动 `scripts/claude_token_guard.sh`（lazy-wake 已落盘可复用）
- ❌ 不动 `scripts/preflight_claude_auth.sh`
- ❌ 不动 `scripts/docker-entrypoint.sh`
- ❌ 不写新 `lib/creds_sync.sh`（v1 spec 需求作废）
- ❌ 不动 ClaudeTeam（host 侧）

### 5.3 验收口径

- compose 改完 → `docker compose -f docker-compose.yml -f docker-compose.live-smoke.override.yml config --quiet` 不报错
- 修法 A 完成后，live-smoke profile 启动时**不**带任何凭证 user override → 容器以 root 跑，host /login 直通

---

## 6. 风险（极简版）

| 风险                                                                  | 影响 | 防御                                                                |
|-----------------------------------------------------------------------|------|---------------------------------------------------------------------|
| 修法 A 后 live-smoke 失去本地 `.claude-credentials/` 隔离              | 低   | 老板已说 restructure 是验证场，host /login 凭证就是要复用，无需隔离 |
| host /login 行为依赖 docker mount 的 rename 跟随                       | 低   | linux 行为已成熟；qa_smoke §4 会触发 device flow 实测，灰度即验证   |
| 容器 root 拥有 host 凭证读权限                                          | 低   | dev compose 行 40-43 注释已显式接受这个 trade-off                   |
| 老板将来又要恢复 prod-hardened 模式                                    | 低   | v1 spec 全文保留，路径不冲突，原文档随时可重用                      |
| live-smoke override 文件被外部脚本/CI 引用（删了会断）                  | 中   | coder 实施前 grep 一遍仓内引用；如有引用走修法 B（保文件改内容）    |

---

## 7. 后续路线（不在本轮）

- 如果未来有"host 不能 /login，必须容器内 OAuth"需求，再考虑 v1 里的 stage + chown 路径
- 如果未来要把 dev compose 的 `user: 0:0` 收紧到 999，再回到"uid 映射 + chown"那套（届时本 spec 作废，重写）
- prod-hardened 复活的话，回头取 v1 spec 落地

---

## 8. 与 v1 的关系

| 项                          | v1 (作废前提)                              | v2 (本文)                                  |
|-----------------------------|--------------------------------------------|--------------------------------------------|
| 主战场                      | prod-hardened（独立 creds 副本）           | dev + live-smoke（host 直挂）              |
| 改动量                      | compose + guard + entrypoint + lib + 测试 | 1 个 yaml 改 + 1 段文档                    |
| 新代码                      | `lib/creds_sync.sh`、guard 扩展            | 无                                         |
| uid 处理                    | chown 999:999                              | root 容器，无 uid 问题                     |
| 灰度时长                    | 6 小时观察周期                              | 1 轮 5 步即过                              |
| 灾备/告警                   | guard 30min                                | 沿用 lazy-wake P0 的 guard（不重写）       |

v1 文件保留 `docs/architect_creds_persistence_2026-04-25.md` 作为"prod-hardened 复活时"的预案，本 spec 文件 `_v2_` 是当前生效版本。

---

*architect · v2 简化版 30 分钟出稿 · 2026-04-25*
*下一跳：老板过目 → coder 1 个 yaml 改 + 1 段文档 → qa_smoke 真机 5 步验收。*
