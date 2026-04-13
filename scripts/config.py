#!/usr/bin/env python3
"""
配置中心 — ClaudeTeam 项目

Agent 团队定义从项目根目录 team.json 读取。
飞书认证由 lark-cli 管理（lark-cli config init）。
每个项目使用独立的 lark-cli profile，避免多项目部署冲突。
"""
import sys as _sys, os as _os, json as _json

# 项目根目录
PROJECT_ROOT = _os.path.dirname(_os.path.dirname(_os.path.abspath(__file__)))
CONFIG_FILE  = _os.path.join(PROJECT_ROOT, "scripts", "runtime_config.json")

# ── Agent 团队定义（从 team.json 读取）─────────────────────────

def _load_team():
    _team_file = _os.path.join(PROJECT_ROOT, "team.json")
    if not _os.path.exists(_team_file):
        print("⚠️  team.json 尚未创建。", file=_sys.stderr)
        print("   如果你正在首次使用 ClaudeTeam，请用 Claude Code 打开本项目，", file=_sys.stderr)
        print("   它会自动引导你完成团队配置。", file=_sys.stderr)
        print(f"   或手动创建: {_team_file}", file=_sys.stderr)
        return {"agents": {}, "session": "ClaudeTeam"}
    with open(_team_file) as _f:
        return _json.load(_f)

_TEAM = _load_team()
AGENTS = _TEAM.get("agents", {})
TMUX_SESSION = _TEAM.get("session", "ClaudeTeam")

# ── runtime_config.json 统一访问 ────────────────────────────────

_runtime_cfg = None

def load_runtime_config():
    """加载 runtime_config.json（带内存缓存）。"""
    global _runtime_cfg
    if _runtime_cfg is None:
        if _os.path.exists(CONFIG_FILE):
            with open(CONFIG_FILE) as _f:
                _runtime_cfg = _json.load(_f)
        else:
            print("❌ 未找到 runtime_config.json，请先运行 python3 scripts/setup.py")
            _sys.exit(1)
    return _runtime_cfg

def save_runtime_config(cfg):
    """保存 runtime_config.json 并刷新内存缓存。"""
    global _runtime_cfg
    _runtime_cfg = cfg
    with open(CONFIG_FILE, "w") as _f:
        _json.dump(cfg, _f, indent=2, ensure_ascii=False)


# ── lark-cli profile 隔离 ──────────────────────────────────────

def _detect_lark_profile():
    """解析当前应使用的 lark-cli profile 名称。

    优先级:
      1) LARK_CLI_PROFILE 环境变量 (显式 override,用于多团队同机部署)
      2) runtime_config.json 的 lark_profile 字段 (setup.py 初始化时写入)
      3) None = 让 lark-cli 使用其默认 profile

    runtime_config.json 里 lark_profile 可能被写成 null (历史遗留),读出来
    是 Python None,直接拼进命令行会变成 "--profile None" 让 lark-cli 报错。
    这里用 `or None` 把空串和 None 都规范化,上游就不会看到歧义值。
    """
    # 1) 环境变量 override — 支持 `LARK_CLI_PROFILE=xxx python3 scripts/setup.py`
    env_profile = _os.environ.get("LARK_CLI_PROFILE", "").strip()
    if env_profile:
        return env_profile
    # 2) runtime_config.json
    if _os.path.exists(CONFIG_FILE):
        try:
            with open(CONFIG_FILE) as _f:
                val = _json.load(_f).get("lark_profile")
            return val or None
        except Exception:
            pass
    return None


def get_lark_cli(profile=None):
    """返回带 --profile 的 lark-cli 命令前缀列表。"""
    p = profile or _detect_lark_profile()
    base = ["npx", "@larksuite/cli"]
    return base + ["--profile", p] if p else base


# 全局常量：其他脚本 from config import LARK_CLI 即可使用
LARK_CLI = get_lark_cli()
