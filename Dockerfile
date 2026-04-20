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

# Claude Code CLI
RUN npm install -g @anthropic-ai/claude-code

# uv — Python 版本管理 + 包管理（kimi-cli 需要 Python >=3.12,容器是 3.11）
RUN curl -LsSf https://astral.sh/uv/install.sh | sh \
    && export PATH="$HOME/.local/bin:$PATH" \
    && uv tool install kimi-cli --python 3.12 \
    && ln -sf /root/.local/share/uv/tools/kimi-cli/bin/kimi /usr/local/bin/kimi

# Codex CLI (OpenAI)
RUN npm install -g @openai/codex || true

# Gemini CLI (Google)
RUN npm install -g @google/gemini-cli || true

# lark-cli（飞书 API）
RUN npm install -g @larksuite/cli \
    && npx @larksuite/cli --version

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

# Claude Code 首次启动配置：跳过主题选择菜单 + 跳过危险权限提示
# 没有这一步,每个新容器的 manager 窗口都会卡在 "Choose the text style" 交互菜单,
# 因为 tmux send-keys 发送的初始化消息会被当成菜单键盘输入吞掉。
# 同时写到 /home/claudeteam/.claude 和 /root/.claude,兼容以两种身份运行容器。
RUN for H in /home/claudeteam /root; do \
      mkdir -p "$H/.claude" && \
      echo '{"theme":"dark","hasCompletedOnboarding":true,"skipDangerousModePermissionPrompt":true}' \
        > "$H/.claude/settings.json" ; \
    done && chown -R claudeteam:claudeteam /home/claudeteam/.claude

# lark-cli skills 安装（全局，容器构建时执行）
# 失败即构建失败 — 如果 skills 确实非必需,可删除整行而不是用 `|| true` 吞错
RUN npx skills add larksuite/cli -g -y

# 切换到非 root 用户
USER claudeteam

# ── 健康检查 ──────────────────────────────────────────────────
# 直接从 team.json 读 session 名,没有兜底 — 兜底只会掩盖 team.json 缺失的配置错误
HEALTHCHECK --interval=60s --timeout=10s --retries=3 \
    CMD tmux has-session -t "$(python3 -c 'import json; print(json.load(open("team.json"))["session"])')" || exit 1

# ── 入口 ──────────────────────────────────────────────────────
# 默认启动团队；容器前台进程为 tmux，保持容器存活
ENTRYPOINT ["bash", "scripts/docker-entrypoint.sh"]
