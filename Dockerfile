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
#   NAT timeouts; on macOS/Windows Docker Desktop, default bridge
#   works but lark-cli round-trips are slower.

# kimi-cli ≥1.0 requires Python ≥3.12; pyproject's
# requires-python = ">=3.10" stays compatible.
FROM python:3.12-slim

# Pin apt index once; install in one layer to keep the image lean.
# `curl` is required by @larksuite/cli's postinstall script (downloads
# a platform-specific binary blob); slim image doesn't ship it.
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

# Pre-install lark-cli at build time so the first `claudeteam router`
# invocation doesn't have to fetch+install ~600 deps on cold start.
# A fresh-container `npx` install can fail under slim-image conditions
# (rc=1, install.js error) and router would exit immediately.
RUN npm install --silent --global @larksuite/cli@latest \
    && lark-cli --version

# Install Claude Code CLI so manager + worker_cc panes can actually run
# an agent. Auth: ANTHROPIC_API_KEY env (passed through compose) or
# interactive `claude /login` once inside the container — tokens
# persist via the /root/.claude volume across restarts.
RUN npm install --silent --global @anthropic-ai/claude-code \
    && claude --version

# Pre-set claude's global settings so `claude --dangerously-skip-
# permissions` (used by spawn_cmd) never pops the "Yes, I accept"
# dialog, never asks per-tool permission, and skips onboarding +
# theme picker on a fresh container.
RUN mkdir -p /root/.claude \
    && printf '%s\n' \
       '{' \
       '  "skipDangerousModePermissionPrompt": true,' \
       '  "hasCompletedOnboarding": true,' \
       '  "theme": "dark",' \
       '  "permissions": {' \
       '    "allow": ["Bash", "Edit", "Read", "Write"]' \
       '  }' \
       '}' > /root/.claude/settings.json

# Install Codex CLI + Kimi CLI. Same pattern as claude-code: install
# binaries here, mount host's auth state at runtime via compose so
# container reuses an already-logged-in session.
#   - codex auth: ~/.codex/auth.json (ChatGPT OAuth)
#   - kimi auth:  ~/.kimi/credentials/<cli>.json
RUN npm install --silent --global @openai/codex \
    && codex --version
RUN pip install --no-cache-dir kimi-cli \
    && kimi --version

# Install `uv` to pull `codex-cli-usage` — the only path to real
# usage percentages for Codex (`/usage` slash card depends on it).
# Symlink the venv bin into /usr/local/bin so the subprocess
# shell-out from feishu/slash finds it on PATH without
# $HOME/.local/bin needing to be present at runtime.
RUN curl -LsSf https://astral.sh/uv/install.sh | sh \
    && export PATH="$HOME/.local/bin:$PATH" \
    && uv tool install codex-cli-usage \
    && ln -sf /root/.local/share/uv/tools/codex-cli-usage/bin/codex-cli-usage /usr/local/bin/codex-cli-usage \
    && codex-cli-usage --help > /dev/null

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
    CLAUDETEAM_CONFIG_FILE=/data/claudeteam.toml \
    CLAUDETEAM_TEAM_FILE=/data/team.json \
    CLAUDETEAM_RUNTIME_CONFIG=/data/runtime_config.json \
    LARK_CLI_NO_PROXY=1

VOLUME ["/data", "/root/.lark-cli"]

# Default to a shell so operators attach with `docker exec -it … bash`
# and run `claudeteam up` / `claudeteam health` manually. A bare
# `claudeteam up` as CMD would exit immediately because tmux runs
# detached and the container would have no foreground process.
CMD ["bash"]
