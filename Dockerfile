###############################################################################
# ClaudeTeam — Multi-Agent Team Container
#
# 单容器方案：所有 Agent + Router + Watchdog 在同一容器内通过 tmux 管理。
# 理由：Agent 间通过 tmux 注入通讯，必须共享 tmux session。
#
# 构建：docker build -t claudeteam .
# 运行：docker compose up -d
###############################################################################

# ── Stage 1: Base ─────────────────────────────────────────────
FROM node:22-bookworm-slim AS base

# 系统依赖：tmux + Python3 + 常用工具
RUN apt-get update && apt-get install -y --no-install-recommends \
    tmux \
    python3 \
    python3-pip \
    python3-venv \
    git \
    curl \
    jq \
    procps \
    && rm -rf /var/lib/apt/lists/*

# ── Stage 2: Dependencies ────────────────────────────────────
FROM base AS deps

# Claude Code CLI is opt-in for legacy/dev images. The default smoke/hardened
# image keeps the runtime CLI surface to codex/kimi/gemini plus lark-cli.
ARG CLAUDETEAM_INSTALL_CLAUDE_CODE=0
RUN if [ "$CLAUDETEAM_INSTALL_CLAUDE_CODE" = "1" ]; then \
      npm install -g @anthropic-ai/claude-code ; \
    else \
      echo "Claude Code install skipped (CLAUDETEAM_INSTALL_CLAUDE_CODE=0)" ; \
    fi

# uv — Python 版本管理 + 包管理（kimi-cli 需要 Python >=3.12,容器是 3.11）
RUN curl -LsSf https://astral.sh/uv/install.sh | sh \
    && export PATH="$HOME/.local/bin:$PATH" \
    && uv tool install kimi-cli --python 3.12 \
    && ln -sf /root/.local/share/uv/tools/kimi-cli/bin/kimi /usr/local/bin/kimi \
    && uv tool install codex-cli-usage \
    && ln -sf /root/.local/share/uv/tools/codex-cli-usage/bin/codex-cli-usage /usr/local/bin/codex-cli-usage \
    && uv tool install gemini-cli-usage \
    && ln -sf /root/.local/share/uv/tools/gemini-cli-usage/bin/gemini-cli-usage /usr/local/bin/gemini-cli-usage

# Codex CLI (OpenAI)
# Fail the image build if the JS wrapper or native optional dependency is
# missing. Otherwise the first prod-hardened agent launch reaches Codex's own
# runtime remediation path and exits on read-only rootfs.
RUN npm install -g @openai/codex --include=optional \
    && node -e "require.resolve('/usr/local/lib/node_modules/@openai/codex/package.json'); require.resolve('/usr/local/lib/node_modules/@openai/codex/node_modules/@openai/codex-linux-x64/package.json')" \
    && codex --version

# Gemini CLI (Google)
RUN npm install -g @google/gemini-cli || true

# lark-cli（飞书 API）
# Do not run `npx` during the default image build. `npx` may fetch and execute
# remote packages outside the pinned Dockerfile dependency list. Live Feishu
# commands can still invoke the installed CLI at runtime through the existing
# adapter path when explicitly enabled.
RUN npm install -g @larksuite/cli

# ── Stage 3: Runtime ─────────────────────────────────────────
FROM deps AS runtime

# 创建非 root 用户（安全最佳实践）
RUN groupadd -r claudeteam && useradd -r -g claudeteam -m -s /bin/bash claudeteam

# 工作目录
WORKDIR /app

# 复制项目文件（排除 .dockerignore 中的内容）
COPY --chown=claudeteam:claudeteam . .

# 创建运行时目录结构
RUN mkdir -p agents workspace/shared/tasks workspace/shared/images \
    && chown -R claudeteam:claudeteam agents workspace

# tmux 配置：增加历史缓冲区 + 鼠标支持
RUN echo 'set -g history-limit 50000' > /home/claudeteam/.tmux.conf \
    && echo 'set -g mouse on' >> /home/claudeteam/.tmux.conf \
    && chown claudeteam:claudeteam /home/claudeteam/.tmux.conf

# Legacy/dev only: preseed Claude Code settings when the image explicitly opts
# into installing that CLI. Default smoke/hardened images do not create these
# home config directories.
ARG CLAUDETEAM_INSTALL_CLAUDE_CODE=0
RUN if [ "$CLAUDETEAM_INSTALL_CLAUDE_CODE" = "1" ]; then \
      for H in /home/claudeteam /root; do \
        mkdir -p "$H/.claude" && \
        echo '{"theme":"dark","hasCompletedOnboarding":true,"skipDangerousModePermissionPrompt":true}' \
          > "$H/.claude/settings.json" ; \
      done && chown -R claudeteam:claudeteam /home/claudeteam/.claude ; \
    else \
      echo "Claude Code settings skipped (CLAUDETEAM_INSTALL_CLAUDE_CODE=0)" ; \
    fi

# Optional lark skill installation is intentionally not part of the default
# production build. If an operator needs it for a dev image, run the install as
# an explicit, audited step outside the hardened build path.

# 切换到非 root 用户
USER claudeteam

# ── 健康检查 ──────────────────────────────────────────────────
# 直接从 team.json 读 session 名,没有兜底 — 兜底只会掩盖 team.json 缺失的配置错误
HEALTHCHECK --interval=60s --timeout=10s --retries=3 \
    CMD tmux has-session -t "$(python3 -c 'import json; print(json.load(open("team.json"))["session"])')" || exit 1

# ── 入口 ──────────────────────────────────────────────────────
# 默认启动团队；容器前台进程为 tmux，保持容器存活
ENTRYPOINT ["bash", "scripts/docker-entrypoint.sh"]
