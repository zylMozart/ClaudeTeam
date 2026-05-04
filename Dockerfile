# ClaudeTeam runtime image — minimum viable.
#
# Bakes in Python 3.11 + tmux + nodejs/npm (for npx @larksuite/cli) + git
# + the claudeteam package itself. Does NOT include the agent CLIs
# (claude / codex / kimi) — each has its own auth and licence
# requirement; derive from this image and add whichever you need.
#
# Volumes:
#   /data          - team config + runtime state (mount a host dir)
#   /root/.lark-cli - lark-cli OAuth profile (mount your existing one)
#
# Network:
#   lark-cli's event +subscribe long-poll needs to reach
#   open.larksuite.com / open.feishu.cn. Run the container with
#   --network host (or compose `network_mode: host`) on Linux to avoid
#   NAT timeouts; on macOS/Windows Docker Desktop, default bridge works
#   but expect the slower lark-cli round-trips noted in CLAUDE.md
#   (project_lark_cli_slow.md memory).

# R170: bumped from 3.11 to 3.12 because kimi-cli ≥1.0 requires Python
# ≥3.12 (older 0.34 still on 3.11 but lacks the slash-command surface
# we need). pyproject's `requires-python = ">=3.10"` stays compatible.
FROM python:3.12-slim

# Pin apt index once; install in one layer to keep the image lean.
# `curl` is required by @larksuite/cli's postinstall script (downloads
# a platform-specific binary blob via curl into node_modules); slim
# image doesn't ship it. Round-58 smoke caught this — without curl
# the npm install errors out with "spawnSync curl ENOENT".
RUN apt-get update \
    && apt-get install -y --no-install-recommends \
        tmux \
        nodejs \
        npm \
        git \
        curl \
        ca-certificates \
        procps \
    && rm -rf /var/lib/apt/lists/*
# `procps` ships `ps` / `uptime` / `free`. Without it the slim image
# has none of those binaries and `_agent_usage` (ps walk for per-agent
# CPU+RSS) returns zero for every agent — boss saw "manager 0.0% / 0 B"
# in /health card 2026-05-04 even though the panes were running.
# /proc-direct fallbacks added for `_host_cpu` / `_host_mem`, but `ps`
# is the cleanest path for per-pid CPU% (kernel-computed, no two-
# snapshot delta required).

# Pre-install lark-cli into npm's global prefix at build time so the
# first `claudeteam router` invocation doesn't have to fetch+install
# +run install.js for ~600 deps on cold start. Round-58 smoke caught
# this: in a fresh container the install.js script fails (rc=1) under
# slim image conditions and router exits immediately. Globally
# installing once at build time means npx resolves to the cached copy
# instantly and no install.js runs at request time.
#
# Pinned to >=1.0.21 (the version host smoke validated against) but
# allow patches; --silent reduces build log noise.
RUN npm install --silent --global @larksuite/cli@latest \
    && lark-cli --version

# R168: install Claude Code CLI so manager + worker_cc panes can
# actually run an agent. Without this, the panes spawn the bash-side
# `claude --dangerously-skip-permissions ...` command and immediately
# get `claude: command not found` (boss saw this in /tmux output).
#
# Auth: token-based via ANTHROPIC_API_KEY env (passed through compose)
# OR interactive `claude /login` once inside the container — tokens
# persist via the /root/.claude volume so subsequent container restarts
# pick them up automatically.
#
# Pinning to a fixed version keeps the smoke environment reproducible;
# bump as needed when host operator upgrades to match.
RUN npm install --silent --global @anthropic-ai/claude-code \
    && claude --version

# R172.b: write claude's global silent-launch settings so the
# `claude --dangerously-skip-permissions` invocation in spawn_cmd
# never pops the "Yes, I accept" dialog and never asks per-tool
# permission. Boss-provided 2026-05-04 (effect: 启动不弹确认、
# 运行中不弹权限、不弹问卷、不弹更新).
#
# `/root/.claude/.credentials.json` is bind-mounted from host RW for
# OAuth state, so this file gets written first to a path the bind
# mount doesn't shadow. ~/.claude/settings.json is OUTSIDE the
# .credentials.json bind path, so this works.
RUN mkdir -p /root/.claude \
    && printf '%s\n' \
       '{' \
       '  "skipDangerousModePermissionPrompt": true,' \
       '  "hasCompletedOnboarding": true,' \
       '  "permissions": {' \
       '    "allow": ["Bash()", "Edit()", "Read()", "Write()", "Edit(.claude/)", "Write(.claude/)"]' \
       '  }' \
       '}' > /root/.claude/settings.json

# R170: install Codex CLI (OpenAI) + Kimi CLI (Moonshot AI). Same
# pattern as claude-code: install the binaries here, mount host's
# auth state at runtime via docker-compose volumes so container
# reuses an already-logged-in session without re-OAuth.
#
# - codex: `npm install -g @openai/codex` ships the node wrapper +
#   platform binary; auth state lives in $HOME/.codex/auth.json
#   (ChatGPT OAuth tokens). Same mount pattern as claude.
# - kimi: Python tool. Use `pip install kimi-cli`; auth state in
#   $HOME/.kimi/credentials/. Mount host's ~/.kimi for credential reuse.
#   (uv would be cleaner but the slim image doesn't ship it; pip is
#   already there from the python:3.11-slim base.)
RUN npm install --silent --global @openai/codex \
    && codex --version
RUN pip install --no-cache-dir kimi-cli \
    && kimi --version

WORKDIR /app

# Copy only what's needed to install the package — pyproject + src.
# Tests / docs / scenarios stay out of the image to keep it small;
# devs who want the full repo should bind-mount the working tree.
COPY pyproject.toml ./
COPY src/ ./src/

RUN pip install --no-cache-dir -e .

# Defaults so a fresh container has a sensible state layout. Override
# any of these at run time via `docker run -e CLAUDETEAM_STATE_DIR=...`
# or compose `environment:` if you want a different layout.
ENV CLAUDETEAM_STATE_DIR=/data/state \
    CLAUDETEAM_TEAM_FILE=/data/team.json \
    CLAUDETEAM_RUNTIME_CONFIG=/data/runtime_config.json \
    LARK_CLI_NO_PROXY=1

VOLUME ["/data", "/root/.lark-cli"]

# Default to a shell so operators attach with `docker exec -it … bash`
# and run `claudeteam up` / `claudeteam health` manually. A bare
# `claudeteam up` as CMD would exit immediately because tmux runs
# detached and the container would have no foreground process.
CMD ["bash"]
