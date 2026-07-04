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
        git \
        curl \
        ca-certificates \
        procps \
        libdbus-1-3 \
        xz-utils \
        ripgrep \
        fd-find \
    && rm -rf /var/lib/apt/lists/* \
    && ln -sf "$(command -v fdfind)" /usr/local/bin/fd
# `libdbus-1-3`: CodeWhale's prebuilt binary links it at runtime.
# `xz-utils`: unpack the Node tarball below.
# `ripgrep` + `fd` (symlinked from Debian's fdfind): on PATH so pi finds them
# and does NOT download fd/rg into each per-agent ~/.pi/agent/bin — which N
# concurrent pi instances would otherwise race to write (verified).
# nodejs/npm are NOT from apt — Debian trixie ships Node 20, but openclaw
# requires Node >= 22. We drop the official Node 22 tarball into /usr/local
# instead (provides node + npm; the other npm-global CLIs run fine on 22).
ARG NODE_VERSION=22.23.1
ARG TARGETARCH
RUN ARCH="$([ "$TARGETARCH" = "amd64" ] && echo x64 || echo arm64)" \
    && curl -fsSL "https://nodejs.org/dist/v${NODE_VERSION}/node-v${NODE_VERSION}-linux-${ARCH}.tar.xz" -o /tmp/node.tar.xz \
    && tar -xJf /tmp/node.tar.xz -C /usr/local --strip-components=1 \
    && rm /tmp/node.tar.xz \
    && node --version && npm --version
# `procps` ships `ps` / `uptime` / `free`. Without it the slim image
# has none of those binaries and `_agent_usage` (ps walk for per-agent
# CPU+RSS) returns zero for every agent — the /health card then reports
# "0.0% / 0 B" for panes that are actually running.
# /proc-direct fallbacks added for `_host_cpu` / `_host_mem`, but `ps`
# is the cleanest path for per-pid CPU% (kernel-computed, no two-
# snapshot delta required).

# Pre-install lark-cli at build time so the first `claudeteam router`
# invocation doesn't have to fetch+install ~600 deps on cold start.
# A fresh-container `npx` install can fail under slim-image conditions
# (rc=1, install.js error) and router would exit immediately.
RUN npm install --silent --global @larksuite/cli@latest \
    && lark-cli --version

# Install Claude Code CLI + its ACP adapter (the DEFAULT runner for
# claude-code agents: the router's AcpHost drives the CLI headless over
# the Agent Client Protocol; without the adapter binary every claude
# agent fails health and can't run). Auth: ANTHROPIC_API_KEY env
# (passed through compose) or interactive `claude /login` once inside
# the container — tokens persist via the /root/.claude volume.
RUN npm install --silent --global @anthropic-ai/claude-code \
       @zed-industries/claude-code-acp \
    && claude --version && claude-code-acp --version || true

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
RUN npm install --silent --global @openai/codex @zed-industries/codex-acp \
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

# ── Additional agent CLIs (the "new workers"): minimax / opencode /
# codewhale / openclaw / trae / hermes. Each drives DeepSeek via an
# OpenAI-compatible endpoint at runtime (OPENAI_BASE_URL / OPENAI_API_KEY
# passed through compose `environment:`); the per-CLI adapter provisions
# its own config at spawn. Versions pinned to the set verified live in
# tests/scenarios/<cli>.md.

# npm-global CLIs (prebuilt binaries; openclaw is why we need Node >= 22).
# pi (@mariozechner/pi-coding-agent) is a BYOK coding agent with a built-in
# `deepseek` provider — no config file, just flags.
RUN npm install --silent --global \
        opencode-ai@1.17.9 \
        codewhale@0.8.64 \
        openclaw@2026.6.10 \
        @mariozechner/pi-coding-agent@0.73.1 \
    && command -v opencode && command -v codewhale && command -v openclaw \
    && command -v pi

# uv-tool CLIs installed from git. Symlink their entrypoints onto PATH the
# same way codex-cli-usage is, so tmux panes find them without relying on
# $HOME/.local/bin being on the runtime PATH. trae-agent imports `docker`
# + `pexpect` unconditionally, so they must be in its tool venv.
RUN export PATH="$HOME/.local/bin:$PATH" \
    && uv tool install "git+https://github.com/MiniMax-AI/Mini-Agent.git" \
    && uv tool install --with docker --with pexpect "git+https://github.com/bytedance/trae-agent.git" \
    && ln -sf /root/.local/share/uv/tools/mini-agent/bin/mini-agent /usr/local/bin/mini-agent \
    && ln -sf /root/.local/share/uv/tools/trae-agent/bin/trae-cli /usr/local/bin/trae-cli \
    && command -v mini-agent && command -v trae-cli

# Hermes (Nous Research). Use the venv installer with --skip-setup (avoids
# the interactive setup prompt). Do NOT pass --no-venv: it produces a
# self-exec'ing /usr/local/bin/hermes wrapper that hangs forever.
# The installer git-clones its repo, which intermittently fails with a TLS/RPC
# error on a flaky network — retry a few times so one hiccup doesn't sink the
# whole build. (A harmless "ffmpeg not found" prints because apt lists were
# cleaned; hermes runs text-only without it.)
RUN for i in 1 2 3 4; do \
        curl -fsSL https://hermes-agent.nousresearch.com/install.sh | bash -s -- --skip-setup \
        && command -v hermes && break || { echo "hermes install attempt $i failed; retrying"; sleep 5; }; \
    done \
    && command -v hermes

WORKDIR /app

# Copy only what's needed to install the package — pyproject + src.
# Tests / docs / scenarios stay out of the image to keep it small;
# devs who want the full repo should bind-mount the working tree.
COPY pyproject.toml ./
COPY src/ ./src/

RUN pip install --no-cache-dir -e .

# The Feishu Channel sidecar (scripts/feishu_channel) — the event ingress
# (`sidecar.js run`) AND the `claudeteam feishu connect` registration flow that
# replaces the old host-only Playwright bot-creator. Bake its node_modules so
# both work offline in the container. Path matches lark.sidecar_path()'s
# repo-relative resolution (/app/scripts/feishu_channel/sidecar.js).
COPY scripts/feishu_channel/ ./scripts/feishu_channel/
RUN cd scripts/feishu_channel \
    && npm install --omit=dev --silent --no-fund --no-audit

# Domain team templates (claudeteam.toml + per-role playbooks). Bundled so an
# operator — or an agent building a team *inside* the container — can copy a
# starting point (`cp -r templates/software-dev/* /data/`) without the full
# repo. Small (markdown + toml), so unlike tests/docs it earns its image space.
COPY templates/ ./templates/

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
