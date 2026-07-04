<p align="center">
  <a href="DEPLOYMENT_zh.md">← Host 部署</a> · <b>Docker 部署</b>
</p>

# Docker 部署

无头 / 服务器 / 多团队用 Docker。宿主上除了 Docker 什么都不要——镜像已烤进 `claudeteam`、
飞书 sidecar，以及 `claude`/`codex`/`kimi`（+`pi`/`hermes`）。`claude-code` / `codex` 的
ACP 协议适配器（`claude-code-acp` / `codex-acp`——它们的默认 runner 要用）也一并内置了。
通用的配置、模型后端、命令、其余故障排查见 [Host 部署](DEPLOYMENT_zh.md)。

> **macOS：** 先启动 Docker Desktop（`open -a Docker`，等鲸鱼图标稳定）。daemon 没起来之前
> `docker compose` 会报 `failed to connect to the docker API …`——用
> `docker info | grep '^Server:'` 确认。

---

## 第 1 步 · 代码 + 凭证写进 `.env`

```bash
git clone https://github.com/zylMozart/ClaudeTeam.git && cd ClaudeTeam
cp .env.example .env
$EDITOR .env          # 填 FEISHU_APP_ID + FEISHU_APP_SECRET
```

> **还没有 bot/App？——`--quick` 是 Docker 下最自然的选择。**
> `claudeteam feishu connect --quick` 是一次扫码的 PersonalAgent 二维码 —— 终端二维码在
> **这个无头容器里就能用**、零控制台 —— 所以这里走它最省事：直接在容器里建好机器人应用 +
> 团队群 + 凭据 + `chat_id`（上面的 `.env` 凭证编辑可省，见第 4 步）。唯一的代价：飞书不会给
> 个人应用 `im:message.group_msg`，所以**群里要 @bot**（私聊不受影响）。想让机器人群里**不 @**
> 也能回？那要靠不带 flag 的浏览器自动化，而它**在这个无头容器里跑不起来** —— 所以：
>
> 1. **在桌面机上跑 `claudeteam feishu connect`（不带 flag）** —— 在一台 Mac/Windows/Linux
>    桌面机（或某个 host 安装）上，它用**有界面的浏览器**驱动飞书控制台创建 + 配权限 + 发版
>    一个带 `im:message.group_msg` 的自建应用。再把 `state/feishu_app.json` 里的 `app_id` /
>    `app_secret` 复制进这里的 `.env`（`chat_id` 复制进 `claudeteam.toml`，见第 4 步）。
>    `--manual` 是同一个自建应用，只是控制台点击改成你自己在任意浏览器里手点。

## 第 2 步 · 挂载源要先存在（重要）

compose 会 bind-mount 一批宿主路径进容器。**文件类**的挂载源（`~/.claude.json`、
`~/.lark-cli/config.json`）若不存在，Docker 会在那建个**空目录**、把应用搞坏。全新机器先：

```bash
mkdir -p ~/.codex ~/.kimi ~/.claude/projects ~/.lark-cli/cache
touch ~/.claude.json
# 仅 macOS：把 Claude OAuth 从 keychain 落成文件（Linux 本来就是文件）
mkdir -p ~/.claude
security find-generic-password -s "Claude Code-credentials" -w > ~/.claude/.credentials.json
```

> 不跑某个 CLI（比如没装 codex / kimi）？把 `docker-compose.yml` 里对应的挂载行删掉即可。

## 第 3 步 · 构建 + 起容器

```bash
docker compose build && docker compose up -d
```

## 第 4 步 · 容器内配置 + 起团队

```bash
# 还没有群？最省事就在容器里跑 --quick（终端二维码；群里随后要 @bot）。它会把凭证和群
# chat_id 都写好，所以可以完全跳过下面的 init --no-connect 和 .env/chat_id 编辑：
docker compose exec claudeteam claudeteam feishu connect --quick

# 或者，如果你已经在 .env 里填了凭证（比如为了群里免 @ 在桌面机上建好的自建应用），
# init 就用 --no-connect 跳过 connect 引导：
docker compose exec --workdir /data claudeteam claudeteam init --no-connect
$EDITOR team-data/claudeteam.toml       # 设 chat_id + 按你有的 CLI 调整 agent
#   想群里免 @，就在桌面机/host 上跑 `claudeteam feishu connect`（不带 flag）/ `--manual`
#   建好应用，把输出的 oc_... 填进来。

docker compose exec claudeteam claudeteam install-hooks
docker compose exec claudeteam claudeteam up
docker compose exec claudeteam claudeteam health
docker compose exec claudeteam tmux attach -t ClaudeTeam   # 看 pane；Ctrl+B d 脱离
```

**起来了的判据** —— 和 Host 一样：主管在飞书群里跑全员点名（**不是**只看 `health` 绿）。

**拆除：** `docker compose down`（容器停掉，`./team-data/` 里的配置 + 状态还在）。

---

## Compose 挂载

完整列表见 `docker-compose.yml`：`./team-data/`→`/data/`（配置 + 状态）、
`~/.claude/.credentials.json`（Claude OAuth，RW 以便刷新持久化）、`~/.codex`/`~/.kimi`
（各 CLI 凭证）、`~/.lark-cli/...`、`./src/`→`/app/src/`（Python 热重载，改源码不用重建）。
镜像已烤进 `claude`/`codex`/`kimi`（+`pi`/`hermes`）；`gemini`/`qwen` 没烤——需要就从
`claudeteam:dev` 派生后装，或把宿主二进制 bind-mount 进去。

---

## 容器专属故障

### `claudeteam feishu connect` 在容器里报错 / 不弹浏览器

**不带 flag** 的 connect 模式属正常 —— 它要用浏览器自动化操作飞书控制台，需要**有界面的桌面
浏览器**，无头容器没有。在容器里改用 `claudeteam feishu connect --quick`（终端二维码在这能用；
群里随后要 @bot）。只有当你需要机器人群里**不 @** 也能回时，才在**桌面机 / host** 上跑不带 flag
的 `claudeteam feishu connect` / `--manual`，再把它的 `state/feishu_app.json` 凭证（→ `.env`）+
群 `chat_id`（→ `claudeteam.toml`）带进容器。见第 1 步。

### `router` 报 `lark-cli failed (rc=2)` 卡住

catchup 试了 `--as user`，但容器只有 bot OAuth。确保 `CLAUDETEAM_LARK_SEND_AS=bot`
在 `docker-compose.yml` 的 `environment:` 里（自带的 compose 已经有）：
`docker compose exec claudeteam env | grep CLAUDETEAM_LARK_SEND_AS`。

### `.env` 凭证 vs `state/feishu_app.json`

Docker 下 `.env` 里的 `FEISHU_APP_ID` / `FEISHU_APP_SECRET` **覆盖** `state/feishu_app.json`。
确认生效：`docker compose exec claudeteam env | grep FEISHU_APP_ID`。

### `docker compose up -d` 又触发了构建

镜像已存在却还重建，多半是 compose 在补构建。先 `docker compose build` 显式建一次，之后
`up -d` 就直接用已有镜像。

---

通用故障排查（飞书连接、`claude: not found`、router 轮换、主管打转、`say` 报 400、凭证缺失
等）、配置 `claudeteam.toml`、模型后端、命令清单 —— 都见 [Host 部署](DEPLOYMENT_zh.md)。
