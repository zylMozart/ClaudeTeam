# Docker deployment

## 场景

把 ClaudeTeam 跑在容器里，state + 多种 OAuth credentials 通过 volume
持久化。R168/R170 起，基础镜像 **预装** claude-code / codex / kimi-cli
（避免每个派生镜像都得装一遍）；OAuth tokens 走 host bind-mount，从
keychain 取出后直接挂进容器，不需要在容器内重新登录。

## 范围

- 类型：host-live (Docker)
- 凭证：host 上已有 `~/.lark-cli/profiles/<profile>.yaml` (lark-cli login 完成)
- 操作员：boss / devops

## Given

- Docker engine 24+ 在 host 上能跑
- host 上有 `~/teams/projectA/`（含 `team.json` + `runtime_config.json`，
  例如通过 `claudeteam init` 在该目录里生成过）
- host 上有效 `~/.lark-cli/profiles/<profile>.yaml`，profile 的 chat_id
  与 runtime_config.json 对得上
- host 上至少有一个 agent CLI（claude / codex / kimi）— 进容器要么
  通过派生镜像 RUN install，要么 bind-mount `/usr/local/bin/claude`
  这种二进制路径

## When

R172.b: a Makefile bundles the deploy flow so operators don't have to
remember the steps or worry about expired OAuth tokens.

```bash
# One-time: prepare team-data/ + .env (FEISHU_APP_ID + FEISHU_APP_SECRET)
mkdir -p team-data
cp ~/teams/projectA/team.json ~/teams/projectA/runtime_config.json team-data/

# Per-deploy: refresh tokens + rebuild (only if Dockerfile changed) + recreate
make deploy
# → security find-generic-password 取 keychain 最新 claude OAuth
# → docker compose build (no-op if Dockerfile unchanged)
# → docker compose down && up -d
# → claudeteam reset --yes && claudeteam up

# Code-only tweaks (Python edits in src/) hot-reload via bind-mount;
# no rebuild needed — just bounce the daemons:
make reset

# Live smoke check (5 read-only slash) — eyeball the cards in chat:
make smoke
```

Manual flow (if you don't want to use the Makefile):

```bash
docker compose build
docker compose up -d
docker compose exec claudeteam claudeteam install-hooks   # writes .claude/commands/*.md
docker compose exec claudeteam claudeteam up
docker compose exec claudeteam claudeteam health
docker compose exec claudeteam tmux attach -t ContainerA  # eyeball panes
```

## Then

`claudeteam health` 输出绿:

- ✅ `team.json` / `runtime_config.json` 走 /data 卷
- ✅ `chat_id` / `lark_profile` 来自挂载的 runtime_config
- ✅ tmux session 起在容器里
- ⚠️ 每个 agent pane 状态取决于该 CLI 是否在容器 `$PATH` —— 派生
  镜像 / bind-mount / 都没做的话会是 `pane up but CLI not ready yet`
- ✅ router / watchdog 的 pid 文件落到 `/data/state/router.pid` /
  `/data/state/watchdog.pid`
- ✅ 退出 `docker compose down` 后再 `up`，team-data 持久化、`claudeteam health`
  能立即读出上次的 cursor / status 历史

## Why this is here

CLAUDE.md item 18 (Dockerfile + compose) 的最小可行实现。Now (R172.b):

1. **基础镜像装好 claude / codex / kimi**（R168/R170）— derived images
   could still skip the install but baseline ergonomics matter more.

2. **OAuth bind-mounts not in-container login** — keychain reach broken
   on macOS, so the deploy flow extracts OAuth via `security
   find-generic-password` on the host and mounts the resulting file
   read-write into the container. Each `make deploy` refreshes.

3. **Per-agent HOME=/data/agent-home/<agent>** (R172.b) — multiple
   panes sharing one ~/.claude.json corrupted on concurrent writes.
   Each agent now owns its own copy, seeded once from the host's
   ~/.claude.json (mounted RO at /root/host-claude.json).

4. **CMD 是 sleep infinity，不是 `claudeteam up`** — `up` 让 tmux 起
   detached 后立刻退出 host 进程，容器会因为 PID 1 退出而停掉。改成
   sleep 让容器活着，`docker compose exec` 驱动 lifecycle 命令。

## Known caveats

- **macOS keychain → container reach**: lark-cli + claude-code both
  store OAuth in keychain. R161 added a `_fetch_tenant_token`
  fallback for lark-cli (env-driven app_id/secret bootstrap into a
  cached tenant_access_token). R172.b's `make deploy` extracts
  claude's `Claude Code-credentials` keychain entry into
  `~/.claude/.credentials.json` (RW bind-mounted into container) so
  the in-container claude sees the same tokens.
- **Token expiry**: claude tokens expire ~12h; refreshToken auto-renew
  works AS LONG AS the credentials file has a non-empty refreshToken
  AND can be written. R172.b mounts RW so claude can refresh in-place
  and the host keychain stays in sync (... mostly; if the host's
  active claude session also rotates, run `make creds` to re-extract).
- **First-launch dialogs**: claude pops up to 3 onboarding dialogs
  (theme picker / auth-method picker / bypass-permissions confirm)
  on a fresh ~/.claude.json. R172.b auto-Enters them in the
  `wait_until_ready` poll loop; `theme: "dark"` baked into
  /root/.claude/settings.json suppresses the picker on most runs.

## Out of scope

- **多容器编排**：每个 agent 各自一容器、router 单独一容器 etc. ——
  现在的 ClaudeTeam runtime 把 router/watchdog/panes 共享一个 tmux
  session，改成多容器就要重新设计 IPC，留给 future 工作。
- **CI/CD 集成**：smoke conductor 在容器里跑、push 镜像到 registry
  这些都不在 B.3 范围内。手动 build + run 就够最小可行。
- **Windows host**：Docker Desktop 上 host 网络只是部分模拟，lark-cli
  long-poll 可能要换 bridge + port-publish。Linux 上按上面 pattern
  直接能跑；macOS 上 build 需要 `docker build --network host`，并
  受限于 keychain 问题（见 Known caveats）。
