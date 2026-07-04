<p align="center">
  <a href="DEPLOYMENT.md">← Host deploy</a> · <b>Docker deploy</b>
</p>

# Docker Deployment

For headless / server / multi-team setups. Nothing but Docker on the host — the
image bakes in `claudeteam`, the Feishu sidecar, and `claude`/`codex`/`kimi`
(plus `pi`/`hermes`). The ACP protocol adapters for `claude-code` / `codex`
(`claude-code-acp` / `codex-acp` — required by their default runner) are baked in
too. Shared config, model-backend, commands, and the rest of the
troubleshooting live in the [Host deploy](DEPLOYMENT.md) guide.

> **macOS:** start Docker Desktop first (`open -a Docker`, wait for the whale to
> settle). `docker compose` errors `failed to connect to the docker API …` until
> the daemon is up — check with `docker info | grep '^Server:'`.

---

## Step 1 · Code + credentials in `.env`

```bash
git clone https://github.com/zylMozart/ClaudeTeam.git && cd ClaudeTeam
cp .env.example .env
$EDITOR .env          # set FEISHU_APP_ID + FEISHU_APP_SECRET
```

> **No bot/app yet? — `--quick` is the natural Docker default.**
> `claudeteam feishu connect --quick` is a one-scan PersonalAgent QR — the
> terminal QR works **inside this headless container**, zero console — so it's the
> easy path here: it builds the bot app + team group + creds + `chat_id` straight
> from the container (skip the `.env` creds edit above; see Step 4). The one catch:
> Feishu won't grant a personal app `im:message.group_msg`, so **groups need @bot**
> (DMs unaffected). Want the bot to reply in groups **without** an @? That needs the
> no-flag browser automation, which **can't run in this headless container** — so:
>
> 1. **Run `claudeteam feishu connect` (no flag) on a desktop** — on a
>    Mac/Windows/Linux-desktop box (or a host install) it drives the Feishu console
>    in a **headed browser** to create + scope + publish a self-built app with
>    `im:message.group_msg`. Then copy `app_id` / `app_secret` from
>    `state/feishu_app.json` into `.env` here (and its `chat_id` into
>    `claudeteam.toml`, Step 4). `--manual` is the same app via console clicks you
>    do from any browser.

## Step 2 · Mount sources must exist first (important)

Compose bind-mounts a set of host paths into the container. A missing *file*
mount-source (`~/.claude.json`, `~/.lark-cli/config.json`) makes Docker create an
empty *directory* there and breaks the app. On a fresh box, first:

```bash
mkdir -p ~/.codex ~/.kimi ~/.claude/projects ~/.lark-cli/cache
touch ~/.claude.json
# macOS only: materialise Claude OAuth from keychain (Linux: already a file)
mkdir -p ~/.claude
security find-generic-password -s "Claude Code-credentials" -w > ~/.claude/.credentials.json
```

> Not running some CLI (no codex / kimi)? Delete its mount line from
> `docker-compose.yml`.

## Step 3 · Build + start the container

```bash
docker compose build && docker compose up -d
```

## Step 4 · Configure + launch inside the container

```bash
# no group yet? the easy path runs --quick right in the container (terminal QR;
# groups then need @bot). It writes both the creds and the group chat_id, so you
# can skip the init --no-connect + .env/chat_id edits entirely:
docker compose exec claudeteam claudeteam feishu connect --quick

# OR, if you already set creds in .env (e.g. a self-built app you built on a
# desktop host for un-@'d groups), init uses --no-connect to skip the connect flow:
docker compose exec --workdir /data claudeteam claudeteam init --no-connect
$EDITOR team-data/claudeteam.toml       # set chat_id + tweak agents to the CLIs you have
#   for un-@'d groups, build the app on a desktop host with `claudeteam feishu
#   connect` (no flag) / `--manual`, then copy its oc_... here.

docker compose exec claudeteam claudeteam install-hooks
docker compose exec claudeteam claudeteam up
docker compose exec claudeteam claudeteam health
docker compose exec claudeteam tmux attach -t ClaudeTeam   # watch panes; Ctrl+B d to detach
```

**You're up when** — same as Host: the manager runs the roll-call in the Feishu
group (**not** just `health` going green).

**Tear down:** `docker compose down` (container stops; config + state in
`./team-data/` survive).

---

## Compose mounts

Full list in `docker-compose.yml`: `./team-data/`→`/data/` (config + state),
`~/.claude/.credentials.json` (Claude OAuth, RW so refreshes persist),
`~/.codex`/`~/.kimi` (per-CLI creds), `~/.lark-cli/...`, `./src/`→`/app/src/`
(Python hot-reload — edit source without rebuilding). The image bakes in
`claude`/`codex`/`kimi` (plus `pi`/`hermes`); `gemini`/`qwen` are **not** — derive
from `claudeteam:dev` and install those, or bind-mount the host binary.

---

## Container-specific failures

### `claudeteam feishu connect` errors / opens no browser in the container

Expected for the **no-flag** connect mode — it browser-automates the Feishu console
and needs a **headed desktop browser**, which the headless container doesn't have.
In the container, use `claudeteam feishu connect --quick` instead (terminal QR works
here; groups then need @bot). Only if you need the bot to reply in groups **without**
an @: run `claudeteam feishu connect` (no flag) / `--manual` on a **desktop machine /
host** and bring its `state/feishu_app.json` creds (→ `.env`) + group `chat_id`
(→ `claudeteam.toml`) into the container. See Step 1.

### `router` reports `lark-cli failed (rc=2)` and stalls

Catchup tried `--as user` but the container only has bot OAuth. Ensure
`CLAUDETEAM_LARK_SEND_AS=bot` is in `docker-compose.yml`'s `environment:` (the
bundled compose ships it): `docker compose exec claudeteam env | grep CLAUDETEAM_LARK_SEND_AS`.

### `.env` creds vs `state/feishu_app.json`

In Docker, the `.env` `FEISHU_APP_ID` / `FEISHU_APP_SECRET` **override**
`state/feishu_app.json`. Confirm: `docker compose exec claudeteam env | grep FEISHU_APP_ID`.

### `docker compose up -d` re-triggers a build

If an existing image still rebuilds, compose is filling in the build. Run
`docker compose build` explicitly once, then `up -d` reuses the built image.

---

The rest of the troubleshooting (Feishu connect, `claude: not found`, router
rotation, manager looping, `say` HTTP 400, missing creds), plus `claudeteam.toml`
config, model backends, and the command list — all in the
[Host deploy](DEPLOYMENT.md) guide.
