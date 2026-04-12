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
    && rm -rf /var/lib/apt/lists/*

# ── Stage 2: Dependencies ────────────────────────────────────
FROM base AS deps

# Claude Code CLI
RUN npm install -g @anthropic-ai/claude-code

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

# lark-cli skills 安装（全局，容器构建时执行）
RUN npx skills add larksuite/cli -g -y 2>/dev/null || true

# 切换到非 root 用户
USER claudeteam

# ── 健康检查 ──────────────────────────────────────────────────
HEALTHCHECK --interval=60s --timeout=10s --retries=3 \
    CMD tmux has-session -t $(python3 -c "import json; print(json.load(open('team.json'))['session'])" 2>/dev/null || echo claudeteam) 2>/dev/null || exit 1

# ── 入口 ──────────────────────────────────────────────────────
# 默认启动团队；容器前台进程为 tmux，保持容器存活
ENTRYPOINT ["bash", "scripts/docker-entrypoint.sh"]
