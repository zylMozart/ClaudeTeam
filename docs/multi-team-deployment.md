# Running Multiple Teams on One Host

You can run N teams side-by-side on the same machine — each team has its own project directory, its own `team.json`, its own tmux session, its own Feishu group chat. Each team gets its own Feishu App, saved as a **named lark-cli profile**, ensuring true identity isolation.

## Setup

```bash
cd ~/project/teamB

# 1) Create a new Feishu App under a named profile (scan QR, click through)
npx @larksuite/cli config init --new --name teamB

# 2) Run setup.py with the profile override — it writes lark_profile=teamB
#    into runtime_config.json so all subsequent scripts use the right identity.
LARK_CLI_PROFILE=teamB python3 scripts/setup.py

# 3) Start the team as usual
bash scripts/start-team.sh
```

`start-team.sh`, `feishu_router.py`, `watchdog.py` all read `lark_profile` from `runtime_config.json` and pass `--profile <name>` to every `lark-cli` call, so the two teams never share credentials, events, or bot state.

## Docker: isolating containers, volumes, and networks

`docker-compose.yml` intentionally **does not** set `container_name:`. A fixed container name is globally unique, so the second `docker compose up` on the same host would see "container `claudeteam` already exists" and happily recreate it — wiping the first team. Instead, Compose auto-names containers as `<project>-team-1`, where `<project>` comes from `COMPOSE_PROJECT_NAME` (or the current directory basename if unset).

For multi-team hosts, tie the project name to each team's `session` so it shows up clearly in `docker ps`:

```bash
# Preferred: use the shipped script, it exports COMPOSE_PROJECT_NAME=claudeteam-<session> for you
bash scripts/docker-deploy.sh

# Manual path: set it yourself before every docker compose call
cd ~/project/teamA
export COMPOSE_PROJECT_NAME=claudeteam-$(python3 -c 'import json; print(json.load(open("team.json"))["session"])')
docker compose up -d
docker compose exec team tmux attach -t "$(python3 -c 'import json; print(json.load(open("team.json"))["session"])')"
docker compose down
```

The same `COMPOSE_PROJECT_NAME` must be set for every subsequent `docker compose ...` invocation in that shell — otherwise Compose falls back to the directory basename and can't find your containers/volumes. If you bounce between teams frequently, consider a small shell alias per team or put `export COMPOSE_PROJECT_NAME=claudeteam-<session>` at the bottom of the team's `.env` and source it.

Top-level `volumes:` and `networks:` declared in `docker-compose.yml` are already auto-prefixed by the project name, so this change is the single knob that isolates *everything* at once.
