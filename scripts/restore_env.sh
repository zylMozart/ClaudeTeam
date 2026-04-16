#!/bin/bash
set -e
# ============================================================
# 环境恢复脚本 — 服务器重启后运行此脚本恢复所有配置
# 用法: bash /data/zebangcheng/ClaudeTeam/scripts/restore_env.sh
# ============================================================

PERSIST_DIR="/data/zebangcheng/ClaudeTeam/.persist"
HOME_DIR="/home/dev"

echo "=========================================="
echo "  ClaudeTeam 环境恢复脚本"
echo "  持久化目录: $PERSIST_DIR"
echo "=========================================="

# 1. 恢复 ~/.claude 软链接
if [ -d "$PERSIST_DIR/.claude" ]; then
    rm -rf "$HOME_DIR/.claude"
    ln -s "$PERSIST_DIR/.claude" "$HOME_DIR/.claude"
    echo "✅ ~/.claude → $PERSIST_DIR/.claude"
else
    echo "⚠️ $PERSIST_DIR/.claude 不存在，跳过"
fi

# 2. 恢复 ~/.npm 软链接
if [ -d "$PERSIST_DIR/.npm" ]; then
    rm -rf "$HOME_DIR/.npm"
    ln -s "$PERSIST_DIR/.npm" "$HOME_DIR/.npm"
    echo "✅ ~/.npm → $PERSIST_DIR/.npm"
else
    echo "⚠️ $PERSIST_DIR/.npm 不存在，跳过"
fi

# 3. 恢复 ~/.local 软链接 (含 claude 二进制和 lark-cli 配置)
if [ -d "$PERSIST_DIR/.local" ]; then
    rm -rf "$HOME_DIR/.local"
    ln -s "$PERSIST_DIR/.local" "$HOME_DIR/.local"
    echo "✅ ~/.local → $PERSIST_DIR/.local"
else
    echo "⚠️ $PERSIST_DIR/.local 不存在，跳过"
fi

# 4. 恢复 .bashrc (如果被重置)
if [ -f "$PERSIST_DIR/.bashrc_backup" ]; then
    if ! grep -q "PERSIST_DIR" "$HOME_DIR/.bashrc" 2>/dev/null; then
        cp "$PERSIST_DIR/.bashrc_backup" "$HOME_DIR/.bashrc"
        echo "✅ ~/.bashrc 已从备份恢复"
    else
        echo "ℹ️ ~/.bashrc 已包含持久化配置，跳过"
    fi
fi

# 5. 确保 PATH 包含必要路径（含 conda）
export PATH="/data/zebangcheng/conda/bin:$HOME_DIR/.local/bin:$PATH"

# 5.1 确保 .bashrc 中有 conda PATH
if ! grep -q '/data/zebangcheng/conda/bin' "$HOME_DIR/.bashrc" 2>/dev/null; then
    echo 'export PATH=/data/zebangcheng/conda/bin:$PATH' >> "$HOME_DIR/.bashrc"
    echo "✅ conda PATH 已添加到 ~/.bashrc"
else
    echo "ℹ️ ~/.bashrc 已包含 conda PATH，跳过"
fi

# 6. 验证
echo ""
echo "=========================================="
echo "  验证结果"
echo "=========================================="

# 验证软链接
for link in .claude .npm .local; do
    if [ -L "$HOME_DIR/$link" ]; then
        target=$(readlink "$HOME_DIR/$link")
        echo "✅ ~/$link → $target"
    else
        echo "❌ ~/$link 不是软链接"
    fi
done

# 验证 Claude Code
if command -v claude &>/dev/null; then
    echo "✅ claude 命令可用"
else
    echo "⚠️ claude 命令不在 PATH 中"
fi

# 验证 lark-cli
if npx @larksuite/cli --version &>/dev/null 2>&1; then
    echo "✅ lark-cli 可用"
else
    echo "⚠️ lark-cli 不可用（可能需要 npm install）"
fi

# 验证 conda
if [ -d "/opt/conda" ]; then
    echo "✅ /opt/conda 存在（原始位置）"
elif [ -d "/data/zebangcheng/conda" ]; then
    echo "✅ /data/zebangcheng/conda 存在（持久化位置）"
else
    echo "❌ conda 环境不存在，需要重新安装"
    echo "   建议: 从 py310_env.yaml 恢复: conda env create -f /data/zebangcheng/ClaudeTeam/py310_env.yaml"
fi

# 验证 feishu_msg.py
if python3 /data/zebangcheng/ClaudeTeam/scripts/feishu_msg.py 2>&1 | grep -q "用法\|Usage\|feishu"; then
    echo "✅ feishu_msg.py 可运行"
else
    echo "⚠️ feishu_msg.py 可能有问题"
fi

echo ""
echo "=========================================="
echo "  恢复完成！"
echo "=========================================="
