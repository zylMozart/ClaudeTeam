#!/usr/bin/env python3
"""
配置中心 — ClaudeTeam 项目

Agent 团队定义从项目根目录 team.json 读取。
lark-cli 管理飞书认证，不再需要 .env 中的飞书凭据。
"""
import sys as _sys, os as _os, json as _json

# 项目根目录
PROJECT_ROOT = _os.path.dirname(_os.path.dirname(_os.path.abspath(__file__)))
CONFIG_FILE  = _os.path.join(PROJECT_ROOT, "scripts", "runtime_config.json")

# ── 飞书凭据（仅供尚未迁移到 lark-cli 的旧脚本使用）──────────────

def _load_env():
    """从 .env 加载环境变量（过渡期保留，lark-cli 迁移完成后删除）。"""
    _env_path = _os.path.join(PROJECT_ROOT, ".env")
    try:
        from dotenv import load_dotenv
        load_dotenv(_env_path)
    except ImportError:
        if _os.path.exists(_env_path):
            with open(_env_path) as _f:
                for _line in _f:
                    _line = _line.strip()
                    if _line and not _line.startswith("#") and "=" in _line:
                        _k, _, _v = _line.partition("=")
                        _os.environ.setdefault(_k.strip(), _v.strip())

_load_env()

# 过渡期保留：尚未迁移的脚本（router/kanban/hire/setup）仍需这些值
APP_ID     = _os.environ.get("FEISHU_APP_ID", "")
APP_SECRET = _os.environ.get("FEISHU_APP_SECRET", "")
BASE       = _os.environ.get("FEISHU_BASE", "https://open.feishu.cn/open-apis")

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

# Router 轮询间隔（秒）
ROUTER_POLL_INTERVAL = 3   # 每 3 秒轮询群消息

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
